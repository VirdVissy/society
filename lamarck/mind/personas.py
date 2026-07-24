"""Persona loading — immutable birth identities from ``personas/*.toml``.

Each file holds one ``PersonaCard`` (contracts): ``agent_id, name,
temperament, values, quirks, speech_style``. Validation is user-error
territory (config data), so violations raise ``ValueError`` (or pydantic's
``ValidationError`` for structural problems), never assertions:

- ``agent_id`` must equal the filename stem (``a1.toml`` -> ``"a1"``),
  which also makes ids unique within a directory by construction (the
  uniqueness check stays as defense in depth);
- names must be unique across the directory (two founders with one name
  would make rendered speech ambiguous);
- every field must be non-empty: strings non-blank, lists non-empty with
  non-blank entries.

``founder_ids`` is the deterministic spawn-order helper: ids sorted by
numeric suffix (``a1..a8``), never by raw string (which would put ``a10``
before ``a2``).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from lamarck.contracts import PersonaCard

__all__ = ["founder_ids", "load_personas"]

_ID_RE = re.compile(r"^[a-z]+([0-9]+)$")


def _require_non_empty(card: PersonaCard, filename: str) -> None:
    text_fields = {
        "agent_id": card.agent_id,
        "name": card.name,
        "temperament": card.temperament,
        "speech_style": card.speech_style,
    }
    for field, value in text_fields.items():
        if not value.strip():
            raise ValueError(f"persona {filename}: field {field!r} must be non-empty")
    list_fields = {"values": card.values, "quirks": card.quirks}
    for field, items in list_fields.items():
        if not items:
            raise ValueError(f"persona {filename}: field {field!r} must be a non-empty list")
        for i, item in enumerate(items):
            if not item.strip():
                raise ValueError(f"persona {filename}: {field}[{i}] must be non-empty")


def load_personas(dir: Path) -> dict[str, PersonaCard]:  # noqa: A002 (contract-pinned name)
    """Load and validate every ``*.toml`` persona card in *dir*.

    Returns ``{agent_id: PersonaCard}``. Deterministic: files are read in
    sorted filename order (dict insertion order follows it). Raises
    ``ValueError`` on validation failure, pydantic ``ValidationError`` on
    structural failure, ``tomllib.TOMLDecodeError`` on malformed TOML.
    """
    files = sorted(dir.glob("*.toml"))
    if not files:
        raise ValueError(f"no persona files (*.toml) found in {dir}")
    cards: dict[str, PersonaCard] = {}
    names: set[str] = set()
    for path in files:
        with path.open("rb") as f:
            data = tomllib.load(f)
        card = PersonaCard.model_validate(data)
        _require_non_empty(card, path.name)
        if card.agent_id != path.stem:
            raise ValueError(
                f"persona {path.name}: agent_id {card.agent_id!r} does not match "
                f"filename stem {path.stem!r}"
            )
        if card.agent_id in cards:  # unreachable given the stem rule; defense in depth
            raise ValueError(f"persona {path.name}: duplicate agent_id {card.agent_id!r}")
        if card.name in names:
            raise ValueError(f"persona {path.name}: duplicate persona name {card.name!r}")
        names.add(card.name)
        cards[card.agent_id] = card
    return cards


def founder_ids(cards: dict[str, PersonaCard]) -> list[str]:
    """Agent ids in spawn order: sorted by numeric suffix (``a1..a8``).

    Ties on the numeric suffix (distinct prefixes) break by the full id, so
    the order is total and deterministic for any valid input.
    """

    def key(agent_id: str) -> tuple[int, str]:
        match = _ID_RE.fullmatch(agent_id)
        if match is None:
            raise ValueError(
                f"agent_id {agent_id!r} has no numeric suffix (expected e.g. 'a1', 'a12')"
            )
        return (int(match.group(1)), agent_id)

    return sorted(cards, key=key)
