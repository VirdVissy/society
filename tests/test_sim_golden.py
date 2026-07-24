"""Golden acceptance test: the VERBATIM configs/world.toml run, frozen.

The literals below were computed from the first verified run and then PINNED
(SPEC §8): any semantic drift anywhere in the pipeline — canonical JSON,
hash chain, RNG derivation, scheduler order, stub weights, runner layout —
changes the chain head and fails here. If a change is *intended*, re-pin
deliberately and record it in the devlog; never "fix" these to green.

The golden run doubles as the acceptance evidence for Phase 0: full action
coverage, degradation occurring naturally (tier-4/5 stone poverty), replay
green, and byte-identical determinism across fresh engine instances.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from lamarck import __version__
from lamarck.contracts import SCHEMA_VERSION, ActionType, EventKind
from lamarck.engine import config_sha, load_world_config
from lamarck.eventstore import EventStore
from lamarck.sim import compute_run_id, replay, run_sim

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "world.toml"

# ---------------------------------------------------------------- pinned run
GOLDEN_RUN_ID = "903276a6edd8"
GOLDEN_DAYS = 30
GOLDEN_ALIVE = 8
GOLDEN_EVENTS = 2161
GOLDEN_HEAD_SEQ = GOLDEN_EVENTS - 1
GOLDEN_HEAD_HASH = "a362ae0d455b18877b70f7ce7af8f5c95c2527d2c1308974c4efaaa695ab2681"
GOLDEN_FINAL_STATE_SHA = "f990988a98b81c8939d5d43a7a2b20dfbd3be9bb31e2ab425657486025a4aa4a"
# Structural pins (implied by the head hash; kept explicit so a drifted run
# fails with a readable diff instead of only a hash mismatch).
GOLDEN_KIND_COUNTS = {
    "run_started": 1,
    "agent_spawned": 8,
    "day_started": 30,
    "phase_started": 120,
    "action": 1920,
    "ledger_adjust": 81,
    "run_finished": 1,
}  # no agent_died in the golden run (also pinned via GOLDEN_ALIVE == 8)
GOLDEN_DISCOVERIES = 81
GOLDEN_DEGRADED = 101


@pytest.fixture(scope="module")
def golden_run(tmp_path_factory):
    """One golden run per module: (cfg, run_dir, summary, events)."""
    run_dir = tmp_path_factory.mktemp("golden") / "run"
    cfg = load_world_config(CONFIG_PATH)
    summary = run_sim(cfg, run_dir, config_path=CONFIG_PATH)
    with EventStore(run_dir / "events.sqlite3") as store:
        events = list(store.scan())
    return cfg, run_dir, summary, events


def test_pinned_fingerprint(golden_run):
    _, _, summary, _ = golden_run
    assert summary.run_id == GOLDEN_RUN_ID
    assert summary.days_elapsed == GOLDEN_DAYS
    assert summary.events == GOLDEN_EVENTS
    assert summary.head_seq == GOLDEN_HEAD_SEQ
    assert summary.head_hash == GOLDEN_HEAD_HASH
    assert summary.final_state_sha == GOLDEN_FINAL_STATE_SHA
    assert summary.alive_count == GOLDEN_ALIVE
    assert summary.discoveries == GOLDEN_DISCOVERIES
    assert summary.degraded == GOLDEN_DEGRADED


def test_layout_structure(golden_run):
    cfg, _, summary, events = golden_run
    assert Counter(ev.kind.value for ev in events) == GOLDEN_KIND_COUNTS

    first = events[0]
    assert first.kind is EventKind.RUN_STARTED
    assert (first.day, first.tick, first.actor) == (0, 0, "")
    assert first.payload == {
        "run_id": compute_run_id(cfg),
        "config_sha": config_sha(cfg),
        "master_seed": cfg.world.master_seed,
        "schema_version": SCHEMA_VERSION,
        "engine_version": __version__,
    }

    spawns = events[1:9]
    assert [ev.kind for ev in spawns] == [EventKind.AGENT_SPAWNED] * 8
    assert [ev.payload["agent_id"] for ev in spawns] == [f"a{i}" for i in range(1, 9)]
    assert all((ev.qi_delta, ev.stones_delta) == (0, 0) for ev in spawns)

    phase_counts = Counter(
        ev.payload["phase"] for ev in events if ev.kind is EventKind.PHASE_STARTED
    )
    assert phase_counts == {"dawn": 30, "action": 30, "dusk": 30, "night": 30}

    for ev in events:
        if ev.kind is EventKind.LEDGER_ADJUST:
            assert ev.payload["reason"] == "bounty"
            assert ev.stones_delta == cfg.economy.bounties[ev.payload["tier"] - 1]
            assert ev.qi_delta == 0

    last = events[-1]
    assert last.kind is EventKind.RUN_FINISHED
    assert last.payload == {
        "days_elapsed": GOLDEN_DAYS,
        "final_state_sha": GOLDEN_FINAL_STATE_SHA,
    }


def test_action_coverage_and_degradation(golden_run):
    cfg, _, _, events = golden_run
    actions = [ev for ev in events if ev.kind is EventKind.ACTION]
    types_seen = {ev.payload["type"] for ev in actions}
    assert types_seen == {a.value for a in ActionType}

    degraded = [ev for ev in actions if ev.payload.get("degraded")]
    assert len(degraded) == GOLDEN_DEGRADED
    assert degraded, "the golden run must exercise degradation"
    rest_cost = cfg.qi.action_costs[ActionType.REST]
    for ev in degraded:
        assert ev.payload["type"] == "rest"
        assert ev.payload["wanted"]["type"] in {a.value for a in ActionType}
        # The canonical config's allowance never binds, so every golden
        # degradation is stone poverty: a paid (never free) rest.
        assert "free" not in ev.payload
        assert ev.qi_delta == -rest_cost


def test_determinism_byte_identical_second_run(golden_run, tmp_path):
    _, _, summary, _ = golden_run
    cfg = load_world_config(CONFIG_PATH)
    summary2 = run_sim(cfg, tmp_path / "again", config_path=CONFIG_PATH)
    assert (summary2.head_seq, summary2.head_hash) == (summary.head_seq, summary.head_hash)
    assert (summary2.head_seq, summary2.head_hash) == (GOLDEN_HEAD_SEQ, GOLDEN_HEAD_HASH)
    assert summary2.final_state_sha == GOLDEN_FINAL_STATE_SHA


def test_replay_verifies_golden_run(golden_run):
    _, run_dir, _, _ = golden_run
    result = replay(run_dir)
    assert result.ok, result.mismatches
    assert result.mismatches == ()
    assert result.head_hash == GOLDEN_HEAD_HASH
    assert result.recorded_state_sha == GOLDEN_FINAL_STATE_SHA
    assert result.refolded_state_sha == GOLDEN_FINAL_STATE_SHA
    assert result.days_elapsed == GOLDEN_DAYS


def test_run_artifacts(golden_run):
    _, run_dir, summary, _ = golden_run
    # config.toml is a byte-verbatim copy of the source config.
    assert (run_dir / "config.toml").read_bytes() == CONFIG_PATH.read_bytes()
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report == {
        "run_id": summary.run_id,
        "days_elapsed": summary.days_elapsed,
        "events": summary.events,
        "head_seq": summary.head_seq,
        "head_hash": summary.head_hash,
        "final_state_sha": summary.final_state_sha,
        "wall_ms": summary.wall_ms,
        "alive_count": summary.alive_count,
        "qi_total": summary.qi_total,
        "stones_total": summary.stones_total,
    }
    assert isinstance(report["wall_ms"], int)  # no floats anywhere in reports
