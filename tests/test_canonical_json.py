"""Format-lock and property tests for lamarck.eventstore.canonical.

The golden byte literals here are hand-derived from the contract rules and
pinned; if one changes, the canonical form changed — that is a contract
break, never a refactor. Unicode spellings that matter are written with
explicit escapes ("caf\\u00e9" composed vs "cafe\\u0301" decomposed) so no
editor or tool can silently renormalize the source.
"""

from __future__ import annotations

import json
import unicodedata
from enum import Enum, IntEnum, StrEnum

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from lamarck.contracts import EventKind
from lamarck.eventstore import CanonicalError, canonical_bytes, sha256_hex

COMPOSED = "caf\u00e9"  # é as one codepoint (NFC)
DECOMPOSED = "cafe\u0301"  # e + combining acute (NFD)

# ------------------------------------------------------------- golden fixture

GOLDEN_VALUE: dict[str, object] = {
    "π": 3141,  # "π": non-ASCII key, sorts after every ASCII key
    "A": True,
    "a": {"x": DECOMPOSED, "y": COMPOSED, "big": 10**21, "neg": -7},
    "": None,
    "list": ["", "\n", {"k": False}, [], {}],
}
# Hand-derived: keys codepoint-sorted after NFC ("" < "A" < "a" < "list" < "π"),
# both spellings of "café" NFC-normalize to the composed form, separators
# (",", ":"), ensure_ascii=False, UTF-8.
GOLDEN_BYTES = (
    '{"":null,"A":true,'
    '"a":{"big":1000000000000000000000,"neg":-7,"x":"caf\u00e9","y":"caf\u00e9"},'
    '"list":["","\\n",{"k":false},[],{}],'
    '"π":3141}'
).encode("utf-8")


def test_golden_nested_fixture_bytes_pinned() -> None:
    assert canonical_bytes(GOLDEN_VALUE) == GOLDEN_BYTES


def test_golden_fixture_is_stable_across_calls() -> None:
    assert canonical_bytes(GOLDEN_VALUE) == canonical_bytes(GOLDEN_VALUE)


# ------------------------------------------------------------------- scalars


def test_scalars_pinned() -> None:
    assert canonical_bytes(None) == b"null"
    assert canonical_bytes(True) == b"true"
    assert canonical_bytes(False) == b"false"
    assert canonical_bytes(0) == b"0"
    assert canonical_bytes(-1) == b"-1"
    assert canonical_bytes("") == b'""'


def test_bigint_any_magnitude() -> None:
    big = 10**100 + 7
    assert canonical_bytes(big) == str(big).encode("ascii")
    assert canonical_bytes(-big) == str(-big).encode("ascii")


def test_control_char_escaping_pinned() -> None:
    # json escapes below U+0020 plus quote and backslash; nothing else.
    assert canonical_bytes('\x00\n\t"\\') == b'"\\u0000\\n\\t\\"\\\\"'
    assert canonical_bytes("\x7f") == b'"\x7f"'  # DEL is not escaped


# ----------------------------------------------------------------------- NFC


def test_nfc_composed_and_decomposed_canonicalize_identically() -> None:
    assert COMPOSED != DECOMPOSED  # distinct Python strings...
    assert canonical_bytes(COMPOSED) == canonical_bytes(DECOMPOSED)  # ...same canonical bytes
    assert canonical_bytes(DECOMPOSED) == b'"caf\xc3\xa9"'  # and the output is composed


def test_nfc_applies_to_keys_and_nested_values() -> None:
    decomposed_tree = {DECOMPOSED: ["ne\u0301e"]}
    composed_tree = {COMPOSED: ["n\u00e9e"]}
    expected = '{"caf\u00e9":["n\u00e9e"]}'.encode()
    assert canonical_bytes(decomposed_tree) == canonical_bytes(composed_tree) == expected


def test_keys_sorted_by_codepoint_after_nfc() -> None:
    # Pre-NFC, "éx" starts with "e" (U+0065) and would sort before "f";
    # post-NFC it starts with "é" (U+00E9) and must sort after.
    assert canonical_bytes({"e\u0301x": 2, "f": 1}) == '{"f":1,"\u00e9x":2}'.encode()


def test_non_ascii_key_sorting_pinned() -> None:
    value = {"b": 1, "\u00e9": 2, "a": 3, "π": 4, "Z": 5}
    expected = '{"Z":5,"a":3,"b":1,"\u00e9":2,"π":4}'.encode()
    assert canonical_bytes(value) == expected


def test_nfc_key_collision_rejected() -> None:
    colliding = {COMPOSED: 1, DECOMPOSED: 2}
    assert len(colliding) == 2  # distinct keys in Python...
    with pytest.raises(CanonicalError, match="collide"):
        canonical_bytes(colliding)  # ...one key after NFC: refuse, never guess


# ------------------------------------------------------------------ rejection


@pytest.mark.parametrize("bad", [1.0, -0.0, 0.5, float("nan"), float("inf"), float("-inf")])
def test_floats_rejected(bad: float) -> None:
    with pytest.raises(CanonicalError, match="float"):
        canonical_bytes(bad)


def test_integral_float_rejected_even_nested() -> None:
    with pytest.raises(CanonicalError, match="float"):
        canonical_bytes({"payload": {"tiers": [1, 2, 3.0]}})


def test_error_reports_path_of_offending_node() -> None:
    with pytest.raises(CanonicalError) as excinfo:
        canonical_bytes({"payload": {"tiers": [1, 2, 3.0]}})
    assert "['payload']['tiers'][2]" in str(excinfo.value)


@pytest.mark.parametrize(
    "bad",
    [
        b"bytes",
        bytearray(b"ba"),
        (1, 2),
        {1, 2},
        frozenset({1}),
        object(),
        complex(1, 2),
        Ellipsis,
    ],
)
def test_non_canonical_types_rejected(bad: object) -> None:
    with pytest.raises(CanonicalError, match="not canonical"):
        canonical_bytes(bad)


@pytest.mark.parametrize("key", [1, True, None, b"k", (1,), frozenset()])
def test_non_str_dict_keys_rejected(key: object) -> None:
    with pytest.raises(CanonicalError, match="key must be str"):
        canonical_bytes({key: "v"})


def test_lone_surrogate_rejected() -> None:
    with pytest.raises(CanonicalError, match="surrogate"):
        canonical_bytes("\ud800")


def test_self_referential_container_rejected() -> None:
    loop: list[object] = []
    loop.append(loop)
    with pytest.raises(CanonicalError, match="recursion limit|self-referential"):
        canonical_bytes(loop)


# --------------------------------------------------------------------- enums


class _PlainEnum(Enum):
    A = "a"


class _LegacyStrMixin(str, Enum):  # noqa: UP042 — the legacy mixin IS the fixture under test
    # Pre-3.11 style; its __str__ reports "_LegacyStrMixin.X" but the
    # underlying str data is the value — canonical must use the data.
    X = "x-value"


class _Tier(IntEnum):
    THREE = 3


def test_strenum_serializes_as_its_value() -> None:
    assert issubclass(EventKind, StrEnum)
    assert canonical_bytes(EventKind.ACTION) == b'"action"'
    assert canonical_bytes({"kind": EventKind.RUN_STARTED}) == b'{"kind":"run_started"}'


def test_strenum_usable_as_dict_key() -> None:
    assert canonical_bytes({EventKind.ACTION: 1}) == b'{"action":1}'


def test_legacy_str_enum_mixin_serializes_as_data_not_str() -> None:
    assert str(_LegacyStrMixin.X) != "x-value"  # __str__ lies...
    assert canonical_bytes(_LegacyStrMixin.X) == b'"x-value"'  # ...canonical does not


def test_intenum_serializes_as_its_value() -> None:
    assert canonical_bytes(_Tier.THREE) == b"3"
    assert canonical_bytes([_Tier.THREE]) == b"[3]"


def test_plain_enum_rejected() -> None:
    with pytest.raises(CanonicalError, match="not canonical"):
        canonical_bytes(_PlainEnum.A)


# ------------------------------------------------------------------- helpers


def test_empty_containers() -> None:
    assert canonical_bytes({}) == b"{}"
    assert canonical_bytes([]) == b"[]"


def test_sha256_hex_pinned_vectors() -> None:
    assert sha256_hex(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert sha256_hex(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


# ------------------------------------------------------- property (hypothesis)


def _canon_values() -> st.SearchStrategy[object]:
    scalars = st.none() | st.booleans() | st.integers() | st.text(max_size=20)
    return st.recursive(
        scalars,
        lambda children: (
            st.lists(children, max_size=4)
            | st.dictionaries(st.text(max_size=10), children, max_size=4)
        ),
        max_leaves=25,
    )


def _canonical_or_skip(value: object) -> bytes:
    """Canonicalize, or — for the astronomically rare generated NFC key
    collision — assert the rejection is deterministic and skip the example."""
    try:
        return canonical_bytes(value)
    except CanonicalError:
        with pytest.raises(CanonicalError):
            canonical_bytes(value)
        assume(False)  # raises UnsatisfiedAssumption; the line below is unreachable
        raise AssertionError("unreachable") from None  # pragma: no cover


def _nfc_deep(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {unicodedata.normalize("NFC", k): _nfc_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_nfc_deep(v) for v in value]
    return value


@settings(max_examples=75, derandomize=True, deadline=None)
@given(value=_canon_values())
def test_property_deterministic(value: object) -> None:
    assert _canonical_or_skip(value) == canonical_bytes(value)


@settings(max_examples=75, derandomize=True, deadline=None)
@given(value=_canon_values())
def test_property_loads_recanonicalizes_to_same_bytes(value: object) -> None:
    data = _canonical_or_skip(value)
    parsed = json.loads(data.decode("utf-8"))
    assert canonical_bytes(parsed) == data


@settings(max_examples=75, derandomize=True, deadline=None)
@given(value=_canon_values())
def test_property_nfc_prenormalization_is_a_noop(value: object) -> None:
    # Canonicalizing x and canonicalizing NFC(x) must agree byte-for-byte:
    # the encoder already normalizes every string.
    data = _canonical_or_skip(value)
    assert canonical_bytes(_nfc_deep(value)) == data
