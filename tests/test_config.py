"""Tests for lamarck.engine.config: loading, validation, fingerprinting.

Variant configs are built by string-editing the canonical configs/world.toml
into tmp_path (the canonical file is read-only for tests). The config_sha
tests are guarded by importorskip on lamarck.eventstore.canonical — that
module is built concurrently by the eventstore agent; the seam is wired by
the integrator, and everything else here runs without it.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from lamarck.contracts import ActionType
from lamarck.engine.config import config_sha, load_world_config

WORLD_TOML = Path(__file__).resolve().parent.parent / "configs" / "world.toml"


def _variant(tmp_path: Path, transform: Callable[[str], str]) -> Path:
    """Write a transformed copy of the canonical TOML; the transform must
    actually change the text (guards against silently-vacuous tests)."""
    base = WORLD_TOML.read_text(encoding="utf-8")
    changed = transform(base)
    assert changed != base, "variant transform did not change the config text"
    p = tmp_path / "world.toml"
    p.write_text(changed, encoding="utf-8")
    return p


# ------------------------------------------------------------------- loading


def test_load_canonical_world_toml_every_field() -> None:
    cfg = load_world_config(WORLD_TOML)
    assert cfg.world.name == "empty-valley"
    assert cfg.world.master_seed == "0xDE5EEDDE5EEDDE5E"
    assert cfg.world.seed_int() == 0xDE5EEDDE5EEDDE5E
    assert cfg.world.days == 30
    assert cfg.world.rounds_per_day == 8
    assert cfg.world.ticks_per_agent_per_round == 1
    assert cfg.population.founders == 8
    assert cfg.qi.qi_max == 1_200_000
    assert cfg.qi.daily_allowance == 30_000
    assert cfg.qi.action_costs == {
        ActionType.EXPERIMENT: 800,
        ActionType.CONVERSE: 600,
        ActionType.TEACH: 900,
        ActionType.STUDY: 700,
        ActionType.TRADE: 400,
        ActionType.NOTE: 200,
        ActionType.TRAVEL: 500,
        ActionType.MEDITATE: 100,
        ActionType.CHALLENGE: 900,
        ActionType.ATTEMPT_BREAKTHROUGH: 1000,
        ActionType.REST: 50,
    }
    assert cfg.economy.starting_stones == 20
    assert cfg.economy.bounties == [10, 25, 60, 150, 400]
    assert cfg.economy.materials == [1, 2, 5, 12, 30]
    assert cfg.economy.stub_success_permille == [500, 250, 120, 50, 15]


def test_action_costs_keys_are_action_types() -> None:
    """TOML string keys must arrive as ActionType members (pydantic coercion)."""
    cfg = load_world_config(WORLD_TOML)
    assert all(isinstance(k, ActionType) for k in cfg.qi.action_costs)
    assert set(cfg.qi.action_costs) == set(ActionType)


def test_load_accepts_str_path() -> None:
    cfg = load_world_config(str(WORLD_TOML))
    assert cfg.world.name == "empty-valley"


# ---------------------------------------------------------------- validation


def test_missing_action_cost_key_raises_naming_it(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace("rest = 50\n", ""))
    with pytest.raises(ValueError, match=r"missing keys.*rest"):
        load_world_config(path)


def test_extra_action_cost_key_raises_naming_it(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace("rest = 50\n", "rest = 50\nfly = 1\n"))
    with pytest.raises(ValueError, match=r"unexpected keys.*fly"):
        load_world_config(path)


def test_missing_and_extra_action_cost_keys_both_named(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace("rest = 50\n", "fly = 1\n"))
    with pytest.raises(ValueError, match=r"missing keys.*rest.*unexpected keys.*fly"):
        load_world_config(path)


def test_bad_hex_master_seed_raises(tmp_path: Path) -> None:
    path = _variant(
        tmp_path, lambda s: s.replace('master_seed = "0xDE5EEDDE5EEDDE5E"', 'master_seed = "0xZZ"')
    )
    with pytest.raises(ValueError, match="master_seed"):
        load_world_config(path)


def test_master_seed_out_of_u64_range_raises(tmp_path: Path) -> None:
    over_u64 = "0x10000000000000000"  # 2**64
    path = _variant(
        tmp_path,
        lambda s: s.replace('master_seed = "0xDE5EEDDE5EEDDE5E"', f'master_seed = "{over_u64}"'),
    )
    with pytest.raises(ValueError, match="u64"):
        load_world_config(path)


def test_permille_above_1000_raises(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace(
            "stub_success_permille = [500, 250, 120, 50, 15]",
            "stub_success_permille = [500, 250, 1001, 50, 15]",
        ),
    )
    with pytest.raises(ValueError, match=r"stub_success_permille\[2\]"):
        load_world_config(path)


def test_permille_negative_raises(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace(
            "stub_success_permille = [500, 250, 120, 50, 15]",
            "stub_success_permille = [-1, 250, 120, 50, 15]",
        ),
    )
    with pytest.raises(ValueError, match=r"stub_success_permille\[0\]"):
        load_world_config(path)


def test_permille_boundaries_are_inclusive(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace(
            "stub_success_permille = [500, 250, 120, 50, 15]",
            "stub_success_permille = [0, 1000, 120, 50, 15]",
        ),
    )
    cfg = load_world_config(path)
    assert cfg.economy.stub_success_permille == [0, 1000, 120, 50, 15]


def test_missing_section_is_validation_error(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace("[population]\nfounders = 8\n", ""))
    with pytest.raises(ValidationError):
        load_world_config(path)


# -------------------------------------------------------------- fingerprint


def test_config_sha_stable_across_loads() -> None:
    pytest.importorskip(
        "lamarck.eventstore.canonical",
        reason="eventstore.canonical not built yet; integrator wires the seam",
    )
    sha_a = config_sha(load_world_config(WORLD_TOML))
    sha_b = config_sha(load_world_config(WORLD_TOML))
    assert sha_a == sha_b
    assert len(sha_a) == 64
    assert set(sha_a) <= set("0123456789abcdef")


def test_config_sha_changes_when_config_changes(tmp_path: Path) -> None:
    pytest.importorskip(
        "lamarck.eventstore.canonical",
        reason="eventstore.canonical not built yet; integrator wires the seam",
    )
    base_sha = config_sha(load_world_config(WORLD_TOML))
    path = _variant(tmp_path, lambda s: s.replace("days = 30", "days = 31"))
    assert config_sha(load_world_config(path)) != base_sha
