"""Canonical JSON — the one true serialized form (contracts: "CANONICAL JSON").

Every hashed or persisted structure in lamarck goes through
:func:`canonical_bytes`; there is no second serializer. The encoder is total
and deterministic: for any input it either returns bytes that are identical
forever, across platforms and interpreter runs, or raises
:class:`CanonicalError`.

Rules (all MUST, from ``lamarck/contracts.py``):

- Allowed values: ``str | int | bool | None | dict | list``; dict keys must be
  ``str``. ``bool`` is allowed (it subclasses ``int``) and encodes as
  ``true``/``false``; ``int`` is accepted at any magnitude. Instances of
  ``str``/``int`` subclasses (``StrEnum``, ``IntEnum``, legacy
  ``class K(str, Enum)`` mixins — pydantic hands these over) encode as their
  underlying value, bypassing any overridden ``__str__``.
- ``float`` is rejected, including integral floats such as ``1.0``, so
  NaN/Inf are unrepresentable. Fractions are scaled integers by convention.
- All strings — keys and values — are NFC-normalized before encoding.
- Keys sort by codepoint *after* NFC normalization; separators are
  ``(",", ":")``; ``ensure_ascii=False``; output is UTF-8 bytes with no
  surrounding whitespace.

Inputs the contract does not pin are rejected, never guessed at:

- Distinct keys that collide after NFC would make the output depend on dict
  insertion order — CanonicalError.
- Strings containing lone surrogates cannot be UTF-8-encoded — CanonicalError.
- Nesting beyond the recursion limit, or self-referential containers —
  CanonicalError.
- Anything else (bytes, tuple, set, plain Enum, arbitrary objects, non-str
  dict keys) — CanonicalError.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

__all__ = ["CanonicalError", "canonical_bytes", "sha256_hex"]


class CanonicalError(Exception):
    """Raised for any value outside the canonical-JSON domain.

    ``path`` is filled in while the error unwinds out of nested containers
    and locates the offending node (e.g. ``["payload", "tiers", 0]``);
    ``str()`` renders it.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.path: list[str | int] = []

    def __str__(self) -> str:
        base = str(self.args[0])
        if not self.path:
            return base
        trail = "".join(f"[{segment!r}]" for segment in self.path)
        return f"{base} (at value{trail})"


type _Canon = None | bool | int | str | dict[str, _Canon] | list[_Canon]


def _normalize(value: object) -> _Canon:
    """Validate *value* against the canonical domain and return an equivalent
    tree of exact builtin types with every string NFC-normalized."""
    if value is None:
        return None
    if isinstance(value, bool):  # before int: bool subclasses int, encodes true/false
        return value
    if isinstance(value, str):
        # str.__str__ extracts the underlying data even when __str__ is
        # overridden (StrEnum and legacy str-Enum mixins report their value).
        return unicodedata.normalize("NFC", str.__str__(value))
    if isinstance(value, float):
        raise CanonicalError(f"float is not canonical (use scaled ints by convention): {value!r}")
    if isinstance(value, int):
        return int(value)  # exact int at any magnitude; IntEnum encodes as its value
    if isinstance(value, dict):
        out: dict[str, _Canon] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError(
                    f"dict key must be str, got {type(key).__qualname__}: {key!r:.80}"
                )
            nkey = unicodedata.normalize("NFC", str.__str__(key))
            if nkey in out:
                raise CanonicalError(f"keys collide after NFC normalization: {nkey!r:.80}")
            try:
                out[nkey] = _normalize(item)
            except CanonicalError as err:
                err.path.insert(0, nkey)
                raise
        return out
    if isinstance(value, list):
        items: list[_Canon] = []
        for index, item in enumerate(value):
            try:
                items.append(_normalize(item))
            except CanonicalError as err:
                err.path.insert(0, index)
                raise
        return items
    raise CanonicalError(f"type is not canonical: {type(value).__qualname__}: {value!r:.80}")


def canonical_bytes(value: object) -> bytes:
    """Encode *value* to canonical JSON UTF-8 bytes per the module docstring.

    Total and deterministic: returns the unique canonical encoding or raises
    :class:`CanonicalError`; ``canonical_bytes(x) == canonical_bytes(x)``
    byte-for-byte, forever, across platforms.
    """
    try:
        normalized = _normalize(value)
        # allow_nan=False is defense in depth: no float survives _normalize.
        text = json.dumps(
            normalized, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
    except RecursionError:
        raise CanonicalError(
            "nesting exceeds the recursion limit (or the value is self-referential)"
        ) from None
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError as err:
        bad = err.object[err.start : err.end]
        raise CanonicalError(f"string is not UTF-8-encodable (lone surrogate): {bad!r}") from None


def sha256_hex(data: bytes) -> str:
    """SHA-256 of *data* as 64 lowercase hex characters."""
    return hashlib.sha256(data).hexdigest()
