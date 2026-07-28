"""Tests for the Phase-1 live-config loader and BOTH config fingerprints.

The two pinned literals below are the regression teeth of this file:

  - ``PHASE0_WORLD_CONFIG_SHA`` pins ``config_sha`` of the canonical
    ``configs/world.toml`` — the Phase-0 stub fingerprint. Any Phase-1
    change that shifts it (a field added to ``WorldConfig``, a serialization
    tweak, a canonical-JSON drift) breaks this test BEFORE it silently
    invalidates the Phase-0 golden run.
  - ``VALLEY_LIVE_CONFIG_SHA`` pins ``live_config_sha`` of the canonical
    ``configs/valley.toml`` — the fresh Phase-1 golden. It moves only when
    valley.toml or the LiveWorldConfig serialization deliberately changes.

Variant configs are built by string-editing the canonical TOMLs into
tmp_path (the canonical files are read-only for tests), mirroring
tests/test_config.py.
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from lamarck.contracts import ActionType, LiveWorldConfig, WorldConfig
from lamarck.engine.config import (
    config_sha,
    live_config_sha,
    load_live_config,
    load_world_config,
)

_CONFIGS = Path(__file__).resolve().parent.parent / "configs"
WORLD_TOML = _CONFIGS / "world.toml"
VALLEY_TOML = _CONFIGS / "valley.toml"

# Pinned 2026-07-23 (computed once, then frozen — see module docstring).
PHASE0_WORLD_CONFIG_SHA = "24cbbf0eaf5426eb509b8bd1b755c18877e4e9822644e180bc85dd72aa5c66cb"
VALLEY_LIVE_CONFIG_SHA = "f6f6bbd348e03d8a174c2047c37fbeb5a33186e27a18ef60e17eb5ff39a47cc2"


def _variant(tmp_path: Path, transform: Callable[[str], str]) -> Path:
    """Write a transformed copy of the canonical valley TOML; the transform
    must actually change the text (guards against silently-vacuous tests)."""
    base = VALLEY_TOML.read_text(encoding="utf-8")
    changed = transform(base)
    assert changed != base, "variant transform did not change the config text"
    p = tmp_path / "valley.toml"
    p.write_text(changed, encoding="utf-8")
    return p


# ------------------------------------------------------------------- loading


def test_load_canonical_valley_toml_every_field() -> None:
    cfg = load_live_config(VALLEY_TOML)
    assert isinstance(cfg, LiveWorldConfig)
    # Frozen Phase-0 base sections.
    assert cfg.world.name == "wuxing-valley"
    assert cfg.world.master_seed == "0xDE5EEDDE5EEDDE5E"
    assert cfg.world.seed_int() == 0xDE5EEDDE5EEDDE5E
    assert cfg.world.days == 30
    assert cfg.world.rounds_per_day == 8
    assert cfg.world.ticks_per_agent_per_round == 1
    assert cfg.population.founders == 8
    assert cfg.qi.qi_max == 1_200_000
    assert cfg.qi.daily_allowance == 30_000
    assert cfg.qi.action_costs == {
        ActionType.EXPERIMENT: 200,
        ActionType.CONVERSE: 0,
        ActionType.TEACH: 100,
        ActionType.STUDY: 50,
        ActionType.TRADE: 50,
        ActionType.NOTE: 0,
        ActionType.TRAVEL: 500,
        ActionType.MEDITATE: 50,
        ActionType.CHALLENGE: 100,
        ActionType.ATTEMPT_BREAKTHROUGH: 200,
        ActionType.REST: 0,
    }
    assert cfg.economy.starting_stones == 20
    assert cfg.economy.bounties == [10, 25, 60, 150, 400]
    assert cfg.economy.materials == [0, 2, 5, 12, 30]
    assert cfg.economy.stub_success_permille == [500, 250, 120, 50, 15]
    # Phase-1 sections.
    assert cfg.model.backend == "mlx"
    assert cfg.model.model_id == "mlx-community/Qwen3-4B-Instruct-2507-4bit"
    assert cfg.model.max_tokens == 220
    assert cfg.model.reflection_max_tokens == 160
    assert cfg.model.temp_permille == 700
    assert cfg.model.seed == 3735928559
    assert cfg.model.prompt_budget_chars == 14_000
    assert cfg.universe.name == "wuxing"
    assert cfg.universe.seed == "0x57A57A57A57A57A5"
    assert cfg.universe.tiers == 5
    assert cfg.live.locations == ["meadow", "furnace-hall", "cold-spring"]
    assert cfg.live.first_discovery_multiplier == 3


def test_load_live_accepts_str_path() -> None:
    assert load_live_config(str(VALLEY_TOML)).world.name == "wuxing-valley"


def test_stub_world_toml_still_loads_as_world_config() -> None:
    cfg = load_world_config(WORLD_TOML)
    assert type(cfg) is WorldConfig
    assert cfg.world.name == "empty-valley"


def test_stub_loader_ignores_live_sections_in_valley_toml() -> None:
    """The Phase-0 loader must keep working on a live TOML: the extra
    [model]/[universe]/[live] sections are ignored, yielding a plain
    WorldConfig (pydantic default: extra keys ignored)."""
    cfg = load_world_config(VALLEY_TOML)
    assert type(cfg) is WorldConfig
    assert not isinstance(cfg, LiveWorldConfig)
    assert cfg.world.name == "wuxing-valley"
    assert cfg.qi.action_costs[ActionType.TRAVEL] == 500


# --------------------------------------------------------------- fingerprints


def test_phase0_config_sha_regression_pin() -> None:
    """THE Phase-0 pin: no Phase-1 change may shift the stub fingerprint."""
    assert config_sha(load_world_config(WORLD_TOML)) == PHASE0_WORLD_CONFIG_SHA


def test_valley_live_config_sha_golden_pin() -> None:
    """The fresh Phase-1 golden: the full extended model's fingerprint."""
    assert live_config_sha(load_live_config(VALLEY_TOML)) == VALLEY_LIVE_CONFIG_SHA


def test_live_config_sha_stable_across_loads() -> None:
    sha_a = live_config_sha(load_live_config(VALLEY_TOML))
    sha_b = live_config_sha(load_live_config(VALLEY_TOML))
    assert sha_a == sha_b
    assert len(sha_a) == 64
    assert set(sha_a) <= set("0123456789abcdef")


def test_live_sha_covers_more_than_base_sha() -> None:
    """The live fingerprint covers [model]/[universe]/[live]; the stub
    fingerprint of the same file's base sections must differ from it."""
    live_sha = live_config_sha(load_live_config(VALLEY_TOML))
    base_sha = config_sha(load_world_config(VALLEY_TOML))
    assert live_sha != base_sha


def test_live_config_sha_changes_when_live_section_changes(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace("first_discovery_multiplier = 3", "first_discovery_multiplier = 4"),
    )
    assert live_config_sha(load_live_config(path)) != VALLEY_LIVE_CONFIG_SHA


# ---------------------------------------------------------------- validation


def test_duplicate_location_raises(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace(
            'locations = ["meadow", "furnace-hall", "cold-spring"]',
            'locations = ["meadow", "furnace-hall", "meadow"]',
        ),
    )
    with pytest.raises(ValueError, match=r"duplicate.*meadow"):
        load_live_config(path)


def test_empty_string_location_raises(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace(
            'locations = ["meadow", "furnace-hall", "cold-spring"]',
            'locations = ["meadow", "", "cold-spring"]',
        ),
    )
    with pytest.raises(ValueError, match=r"empty-string.*\[1\]"):
        load_live_config(path)


def test_empty_locations_list_is_validation_error(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace(
            'locations = ["meadow", "furnace-hall", "cold-spring"]', "locations = []"
        ),
    )
    with pytest.raises(ValidationError):
        load_live_config(path)


def test_bad_backend_raises(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace('backend = "mlx"', 'backend = "gpt4"'))
    with pytest.raises(ValueError, match=r"model\.backend.*gpt4"):
        load_live_config(path)


def test_scripted_backend_is_accepted(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace('backend = "mlx"', 'backend = "scripted"'))
    assert load_live_config(path).model.backend == "scripted"


def test_bad_universe_seed_hex_raises(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace('seed = "0x57A57A57A57A57A5"', 'seed = "0xZZ"'))
    with pytest.raises(ValueError, match=r"universe\.seed.*hex"):
        load_live_config(path)


def test_universe_seed_out_of_u64_range_raises(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace('seed = "0x57A57A57A57A57A5"', 'seed = "0x10000000000000000"'),
    )
    with pytest.raises(ValueError, match=r"universe\.seed.*u64"):
        load_live_config(path)


def test_first_discovery_multiplier_zero_is_validation_error(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace("first_discovery_multiplier = 3", "first_discovery_multiplier = 0"),
    )
    with pytest.raises(ValidationError):
        load_live_config(path)


def test_live_loader_reuses_action_costs_completeness_check(tmp_path: Path) -> None:
    path = _variant(tmp_path, lambda s: s.replace("rest = 0\n", ""))
    with pytest.raises(ValueError, match=r"missing keys.*rest"):
        load_live_config(path)


def test_live_loader_reuses_master_seed_check(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace('master_seed = "0xDE5EEDDE5EEDDE5E"', 'master_seed = "0xZZ"'),
    )
    with pytest.raises(ValueError, match="master_seed"):
        load_live_config(path)


def test_missing_live_section_is_validation_error(tmp_path: Path) -> None:
    path = _variant(
        tmp_path,
        lambda s: s.replace(
            '[live]\nlocations = ["meadow", "furnace-hall", "cold-spring"]\n'
            "first_discovery_multiplier = 3\n",
            "",
        ),
    )
    with pytest.raises(ValidationError):
        load_live_config(path)
