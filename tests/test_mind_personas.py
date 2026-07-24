"""Tests for lamarck.mind.personas — birth identities and spawn order."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lamarck.contracts import PersonaCard
from lamarck.mind.personas import founder_ids, load_personas

PERSONAS_DIR = Path(__file__).resolve().parent.parent / "personas"

_TEMPLATE = """\
agent_id = "{agent_id}"
name = "{name}"
temperament = "{temperament}"
values = [{values}]
quirks = [{quirks}]
speech_style = "{speech_style}"
"""


def _write(
    directory: Path,
    filename: str,
    agent_id: str,
    name: str,
    temperament: str = "calm",
    values: str = '"a value"',
    quirks: str = '"a quirk"',
    speech_style: str = "plain",
) -> None:
    directory.joinpath(filename).write_text(
        _TEMPLATE.format(
            agent_id=agent_id,
            name=name,
            temperament=temperament,
            values=values,
            quirks=quirks,
            speech_style=speech_style,
        ),
        encoding="utf-8",
    )


# ----------------------------------------------------------- real personas/


def test_real_personas_load() -> None:
    cards = load_personas(PERSONAS_DIR)
    assert len(cards) == 8
    assert set(cards) == {f"a{i}" for i in range(1, 9)}
    for agent_id, card in cards.items():
        assert isinstance(card, PersonaCard)
        assert card.agent_id == agent_id


def test_real_personas_spawn_order() -> None:
    cards = load_personas(PERSONAS_DIR)
    assert founder_ids(cards) == [f"a{i}" for i in range(1, 9)]


def test_real_personas_names_unique_and_fields_non_empty() -> None:
    cards = load_personas(PERSONAS_DIR)
    names = [card.name for card in cards.values()]
    assert len(names) == len(set(names))
    for card in cards.values():
        assert card.name.strip()
        assert card.temperament.strip()
        assert card.speech_style.strip()
        assert card.values and all(v.strip() for v in card.values)
        assert card.quirks and all(q.strip() for q in card.quirks)


def test_real_personas_load_is_deterministic() -> None:
    first = load_personas(PERSONAS_DIR)
    second = load_personas(PERSONAS_DIR)
    assert first == second
    assert list(first) == list(second)


# ------------------------------------------------------- validation failures


def test_agent_id_must_match_filename_stem(tmp_path: Path) -> None:
    _write(tmp_path, "b1.toml", agent_id="a1", name="Someone")
    with pytest.raises(ValueError, match="does not match"):
        load_personas(tmp_path)


def test_duplicate_id_under_wrong_filename_rejected(tmp_path: Path) -> None:
    # Two files cannot share a stem, so a duplicated id necessarily rides a
    # mismatched filename — caught by the stem rule.
    _write(tmp_path, "a1.toml", agent_id="a1", name="First")
    _write(tmp_path, "a2.toml", agent_id="a1", name="Second")
    with pytest.raises(ValueError, match="does not match"):
        load_personas(tmp_path)


def test_duplicate_name_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a1.toml", agent_id="a1", name="Same Name")
    _write(tmp_path, "a2.toml", agent_id="a2", name="Same Name")
    with pytest.raises(ValueError, match="duplicate persona name"):
        load_personas(tmp_path)


def test_empty_string_field_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a1.toml", agent_id="a1", name="Yan", temperament="")
    with pytest.raises(ValueError, match="'temperament' must be non-empty"):
        load_personas(tmp_path)


def test_whitespace_only_field_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a1.toml", agent_id="a1", name="   ")
    with pytest.raises(ValueError, match="'name' must be non-empty"):
        load_personas(tmp_path)


def test_empty_list_field_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a1.toml", agent_id="a1", name="Yan", values="")
    with pytest.raises(ValueError, match="'values' must be a non-empty list"):
        load_personas(tmp_path)


def test_blank_list_entry_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "a1.toml", agent_id="a1", name="Yan", quirks='"ok", "  "')
    with pytest.raises(ValueError, match=r"quirks\[1\] must be non-empty"):
        load_personas(tmp_path)


def test_missing_field_is_structural_error(tmp_path: Path) -> None:
    tmp_path.joinpath("a1.toml").write_text('agent_id = "a1"\nname = "Yan"\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_personas(tmp_path)


def test_empty_directory_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no persona files"):
        load_personas(tmp_path)


# ---------------------------------------------------------------- spawn order


def test_founder_ids_sort_numerically_not_lexically(tmp_path: Path) -> None:
    for i in (1, 2, 10, 11):
        _write(tmp_path, f"a{i}.toml", agent_id=f"a{i}", name=f"Founder {i}")
    cards = load_personas(tmp_path)
    assert founder_ids(cards) == ["a1", "a2", "a10", "a11"]  # 10 after 2, not after 1


def test_founder_ids_requires_numeric_suffix() -> None:
    card = PersonaCard(
        agent_id="odd",
        name="Odd One",
        temperament="strange",
        values=["v"],
        quirks=["q"],
        speech_style="plain",
    )
    with pytest.raises(ValueError, match="numeric suffix"):
        founder_ids({"odd": card})
