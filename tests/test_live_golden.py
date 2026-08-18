"""Golden acceptance test for the Phase-1 LIVE runner, frozen.

The run: configs/valley.toml with ``world.days = 4`` and ``model.backend =
"scripted"`` (everything else verbatim), 8 founders from the repo personas,
ScriptedBackend(tests.scripted_llm.make()), difftest_interval=1, synthesized
run-dir config (config_path=None). The literals below were computed from the
first verified run and then PINNED: any semantic drift anywhere in the live
pipeline — prompt template, envelope hashing, seed-stream discipline,
scheduler order, parser, universe generation, billing, runner layout —
changes the chain head and fails here. If a change is *intended*, re-pin
deliberately; never "fix" these to green.

The golden run doubles as Phase-1 acceptance evidence: >= 3 distinct
verified discoveries, at least one malformed forfeit and one recovered
retry, byte-identical re-runs, deep replay reproducing the identical head,
and tamper detection. The payload KEY SETS asserted here are the ones SPEC
§9a will lock.
"""

import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from lamarck import __version__
from lamarck.contracts import SCHEMA_VERSION, EventKind
from lamarck.engine import live_config_sha, load_live_config
from lamarck.eventstore import EventStore, TextsStore, canonical_bytes
from lamarck.live import compute_live_run_id, replay_live, run_live
from lamarck.mind import TEMPLATE_VERSION, prompt_sha
from lamarck.serving import ScriptedBackend
from tests.scripted_llm import make as make_scripted

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"

# ---------------------------------------------------------------- pinned run
GOLDEN_RUN_ID = "e1fbae05c4f0"
GOLDEN_DAYS = 4
GOLDEN_ALIVE = 8
GOLDEN_EVENTS = 858
GOLDEN_HEAD_SEQ = GOLDEN_EVENTS - 1
GOLDEN_HEAD_HASH = "7ba768ae05a82c1e915205f96e13dc0763b29120eb5afb169d67fcf9056e46a1"
GOLDEN_FINAL_STATE_SHA = "6b2295e3c39d51d4cb084a8cc9fd5142ff4a190c9a1cff4c5a68ca9ea29d0e2d"
GOLDEN_KIND_COUNTS = {
    "run_started": 1,
    "agent_spawned": 8,
    "day_started": 4,
    "phase_started": 16,
    "action": 256,
    "llm_call": 320,
    "task_attempt": 146,
    "reflection": 32,
    "ledger_adjust": 74,  # 66 auto-claims (incl. 2 tier-2 via satchel) + 8 trades
    "run_finished": 1,
}
GOLDEN_ATTEMPTS = 146
GOLDEN_DISCOVERIES = 66  # auto-claims (once per cultivator per commission)
GOLDEN_DISTINCT_DISCOVERIES = 9  # all 8 tier-1 + 1 tier-2 (satchel-crafted); >= 3 required
GOLDEN_FIRST_DISCOVERIES = 9
GOLDEN_DEGRADED = 8
GOLDEN_MALFORMED_FORFEITS = 8  # one doubly-malformed pair per agent (>= 1 required)
GOLDEN_RETRIES = 32
GOLDEN_RETRY_RECOVERED = 24  # malformed/bad-location/float specials recover (>= 1 required)
GOLDEN_LLM_CALLS = 320
GOLDEN_USAGE_IN = 544999
GOLDEN_USAGE_OUT = 4960

# Payload key sets SPEC §9a will lock.
RUN_STARTED_KEYS = {
    "run_id",
    "config_sha",
    "master_seed",
    "schema_version",
    "engine_version",
    "mode",
    "template_version",
    "model_id",
}
AGENT_SPAWNED_KEYS = {"agent_id", "qi_max", "starting_stones", "name"}
LLM_CALL_KEYS = {
    "purpose",
    "agent",
    "model_id",
    "adapter",
    "prompt_sha256",
    "temp_permille",
    "max_tokens",
    "seed",
    "response",
    "usage_in",
    "usage_out",
}
TASK_ATTEMPT_KEYS = {
    "steps",
    "step_products",
    "message",
    "claims",
}
CLAIM_KEYS = {"task_id", "tier", "first"}
BOUNTY_ADJUST_KEYS = {"reason", "task_id", "tier", "first"}


def golden_cfg():
    cfg = load_live_config(VALLEY_TOML)
    return cfg.model_copy(
        update={
            "world": cfg.world.model_copy(update={"days": GOLDEN_DAYS}),
            "model": cfg.model.model_copy(update={"backend": "scripted"}),
        }
    )


@pytest.fixture(scope="module")
def golden_run(tmp_path_factory):
    """One golden live run per module: (cfg, run_dir, summary, events)."""
    run_dir = tmp_path_factory.mktemp("live-golden") / "run"
    cfg = golden_cfg()
    summary = run_live(
        cfg,
        run_dir,
        config_path=None,
        backend=ScriptedBackend(make_scripted()),
        difftest_interval=1,
    )
    with EventStore(run_dir / "events.sqlite3") as store:
        events = list(store.scan())
    return cfg, run_dir, summary, events


def test_pinned_fingerprint(golden_run):
    cfg, _, summary, _ = golden_run
    assert summary.run_id == GOLDEN_RUN_ID == compute_live_run_id(cfg)
    assert summary.days_elapsed == GOLDEN_DAYS
    assert summary.events == GOLDEN_EVENTS
    assert summary.head_seq == GOLDEN_HEAD_SEQ
    assert summary.head_hash == GOLDEN_HEAD_HASH
    assert summary.final_state_sha == GOLDEN_FINAL_STATE_SHA
    assert summary.alive_count == GOLDEN_ALIVE
    assert summary.llm_calls == GOLDEN_LLM_CALLS
    assert summary.usage_in_total == GOLDEN_USAGE_IN
    assert summary.usage_out_total == GOLDEN_USAGE_OUT
    assert summary.reflections == 32
    assert summary.reflections_skipped == 0
    assert summary.spent_skips == 0


def test_coverage_pins(golden_run):
    _, _, summary, _ = golden_run
    assert summary.attempts == GOLDEN_ATTEMPTS
    assert summary.discoveries == GOLDEN_DISCOVERIES
    assert summary.distinct_discoveries == GOLDEN_DISTINCT_DISCOVERIES
    assert summary.distinct_discoveries >= 3, "pins must stay interesting"
    assert summary.first_discoveries == GOLDEN_FIRST_DISCOVERIES
    assert summary.degraded == GOLDEN_DEGRADED
    assert summary.malformed_forfeits == GOLDEN_MALFORMED_FORFEITS
    assert summary.malformed_forfeits >= 1, "the golden run must exercise forfeit"
    assert summary.retries == GOLDEN_RETRIES
    assert summary.retry_recovered == GOLDEN_RETRY_RECOVERED
    assert summary.retry_recovered >= 1, "the golden run must exercise retry recovery"


def test_layout_and_payload_key_sets(golden_run):
    cfg, _, _, events = golden_run
    assert Counter(ev.kind.value for ev in events) == GOLDEN_KIND_COUNTS

    first = events[0]
    assert first.kind is EventKind.RUN_STARTED
    assert (first.day, first.tick, first.actor) == (0, 0, "")
    assert first.payload == {
        "run_id": GOLDEN_RUN_ID,
        "config_sha": live_config_sha(cfg),
        "master_seed": cfg.world.master_seed,
        "schema_version": SCHEMA_VERSION,
        "engine_version": __version__,
        "mode": "live",
        "template_version": TEMPLATE_VERSION,
        "model_id": cfg.model.model_id,
    }
    assert set(first.payload) == RUN_STARTED_KEYS

    spawns = [ev for ev in events if ev.kind is EventKind.AGENT_SPAWNED]
    assert [ev.payload["agent_id"] for ev in spawns] == [f"a{i}" for i in range(1, 9)]
    for ev in spawns:
        assert set(ev.payload) == AGENT_SPAWNED_KEYS
        assert isinstance(ev.payload["name"], str) and ev.payload["name"]
        assert (ev.qi_delta, ev.stones_delta) == (0, 0)

    for ev in events:
        if ev.kind is EventKind.LLM_CALL:
            assert set(ev.payload) == LLM_CALL_KEYS
            assert ev.payload["purpose"] in {"tick", "reflection"}
            assert ev.payload["agent"] == ev.actor
            assert ev.payload["adapter"] == ""
            assert ev.stones_delta == 0
            assert ev.qi_delta < 0
        elif ev.kind is EventKind.TASK_ATTEMPT:
            assert set(ev.payload) == TASK_ATTEMPT_KEYS
            assert all(set(c) == CLAIM_KEYS for c in ev.payload["claims"])
            assert (ev.qi_delta, ev.stones_delta) == (0, 0)
        elif ev.kind is EventKind.REFLECTION:
            assert set(ev.payload) == {"text"}
            assert (ev.qi_delta, ev.stones_delta) == (0, 0)
        elif ev.kind is EventKind.LEDGER_ADJUST:
            if ev.payload["reason"] == "bounty":
                assert set(ev.payload) == BOUNTY_ADJUST_KEYS
                multiplier = cfg.live.first_discovery_multiplier if ev.payload["first"] else 1
                tier = ev.payload["tier"]
                assert ev.stones_delta == (
                    cfg.economy.bounties[tier - 1] * multiplier - cfg.economy.materials[tier - 1]
                )
            else:
                assert set(ev.payload) == {"reason", "from"}
                assert ev.payload["reason"] == "trade"
                assert ev.stones_delta > 0

    last = events[-1]
    assert last.kind is EventKind.RUN_FINISHED
    assert last.payload == {
        "days_elapsed": GOLDEN_DAYS,
        "final_state_sha": GOLDEN_FINAL_STATE_SHA,
    }


def test_first_in_world_unique_per_task(golden_run):
    _, _, _, events = golden_run
    firsts = [
        claim["task_id"]
        for ev in events
        if ev.kind is EventKind.TASK_ATTEMPT
        for claim in ev.payload["claims"]
        if claim["first"]
    ]
    assert len(firsts) == len(set(firsts)) == GOLDEN_FIRST_DISCOVERIES


def test_prompt_texts_side_table(golden_run):
    _, run_dir, _, events = golden_run
    llm_calls = [ev for ev in events if ev.kind is EventKind.LLM_CALL]
    with TextsStore(run_dir / "events.sqlite3") as texts:
        for ev in llm_calls:
            prompt = texts.get(ev.seq)
            assert prompt is not None, f"llm_texts missing seq {ev.seq}"
            assert prompt_sha(prompt) == ev.payload["prompt_sha256"]
            envelope = json.loads(prompt)
            assert set(envelope) == {"system", "template", "user"}
            assert envelope["template"] == TEMPLATE_VERSION


def test_determinism_byte_identical_second_run(golden_run, tmp_path):
    _, _, summary, _ = golden_run
    summary2 = run_live(
        golden_cfg(),
        tmp_path / "again",
        config_path=None,
        backend=ScriptedBackend(make_scripted()),
        difftest_interval=1,
    )
    assert (summary2.head_seq, summary2.head_hash) == (GOLDEN_HEAD_SEQ, GOLDEN_HEAD_HASH)
    assert (summary2.head_seq, summary2.head_hash) == (summary.head_seq, summary.head_hash)
    assert summary2.final_state_sha == GOLDEN_FINAL_STATE_SHA


def test_shallow_replay_ok(golden_run):
    _, run_dir, _, _ = golden_run
    result = replay_live(run_dir)
    assert result.ok, result.mismatches
    assert result.mode == "shallow"
    assert result.head_hash == GOLDEN_HEAD_HASH
    assert result.recorded_state_sha == GOLDEN_FINAL_STATE_SHA
    assert result.refolded_state_sha == GOLDEN_FINAL_STATE_SHA
    assert result.attempts_checked == GOLDEN_ATTEMPTS
    assert result.days_elapsed == GOLDEN_DAYS


def test_deep_replay_reproduces_head(golden_run):
    _, run_dir, _, _ = golden_run
    result = replay_live(run_dir, deep=True)
    assert result.ok, result.mismatches
    assert result.mode == "deep"
    assert (result.replayed_head_seq, result.replayed_head_hash) == (
        GOLDEN_HEAD_SEQ,
        GOLDEN_HEAD_HASH,
    )


def test_tampered_run_fails_shallow_replay(golden_run, tmp_path):
    _, run_dir, _, _ = golden_run
    tampered = tmp_path / "tampered"
    shutil.copytree(run_dir, tampered)
    conn = sqlite3.connect(tampered / "events.sqlite3")
    try:
        seq, payload = conn.execute(
            "SELECT seq, payload FROM events WHERE kind = 'day_started' ORDER BY seq LIMIT 1"
        ).fetchone()
        flipped = canonical_bytes({"day": 999}).decode("utf-8")
        assert flipped != payload
        conn.execute("UPDATE events SET payload = ? WHERE seq = ?", (flipped, seq))
        conn.commit()
    finally:
        conn.close()
    result = replay_live(tampered)
    assert not result.ok
    assert result.mismatches
