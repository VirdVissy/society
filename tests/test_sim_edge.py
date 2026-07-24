"""Edge-of-envelope sim runs: death, allowance starvation, and tampering.

Configs are derived from the canonical configs/world.toml via nested
``model_copy`` so these tests track the real config surface. No hash pins
here — the golden test owns fingerprints; these tests own the *rules*:
deaths happen at dusk and end the run early, degradation (paid and free)
kicks in exactly at the allowance boundary, and a single flipped payload
byte flips the replay verdict.
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

from lamarck.contracts import ActionType, EventKind, WorldConfig
from lamarck.engine import load_world_config
from lamarck.eventstore import EventStore, canonical_bytes
from lamarck.sim import replay, run_sim

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "world.toml"


def _with_world(cfg: WorldConfig, **kw) -> WorldConfig:
    return cfg.model_copy(update={"world": cfg.world.model_copy(update=kw)})


def _with_qi(cfg: WorldConfig, **kw) -> WorldConfig:
    return cfg.model_copy(update={"qi": cfg.qi.model_copy(update=kw)})


def _scan(run_dir: Path):
    with EventStore(run_dir / "events.sqlite3") as store:
        return list(store.scan())


def _tamper_one_payload(run_dir: Path) -> None:
    """Flip one committed payload value (kept canonical, so only the hash
    chain — not payload-text canonicality — catches it)."""
    conn = sqlite3.connect(run_dir / "events.sqlite3")
    try:
        seq, payload = conn.execute(
            "SELECT seq, payload FROM events WHERE kind = 'day_started' ORDER BY seq LIMIT 1"
        ).fetchone()
        tampered = canonical_bytes({"day": 999}).decode("utf-8")
        assert tampered != payload
        cur = conn.execute("UPDATE events SET payload = ? WHERE seq = ?", (tampered, seq))
        assert cur.rowcount == 1
        conn.commit()
    finally:
        conn.close()


def test_death_config_ends_early(tmp_path):
    cfg = _with_qi(load_world_config(CONFIG_PATH), qi_max=20_000)
    run_dir = tmp_path / "death"
    summary = run_sim(cfg, run_dir, difftest_interval=1)  # difftest at every dusk

    assert summary.days_elapsed < cfg.world.days
    assert summary.alive_count == 0

    events = _scan(run_dir)
    died = [ev for ev in events if ev.kind is EventKind.AGENT_DIED]
    assert len(died) == cfg.population.founders
    assert {ev.payload["cause"] for ev in died} == {"qi_exhausted"}
    assert all((ev.qi_delta, ev.stones_delta) == (0, 0) for ev in died)
    assert len({ev.actor for ev in died}) == cfg.population.founders  # each dies once

    # Dead agents take no further slots: no ACTION after an agent's death day.
    death_day = {ev.actor: ev.day for ev in died}
    for ev in events:
        if ev.kind is EventKind.ACTION and ev.actor in death_day:
            assert ev.day <= death_day[ev.actor]

    last = events[-1]
    assert last.kind is EventKind.RUN_FINISHED
    assert last.payload["days_elapsed"] == summary.days_elapsed
    assert replay(run_dir).ok


def test_tiny_allowance_degrades_and_goes_free(tmp_path):
    cfg = load_world_config(CONFIG_PATH)
    cfg = _with_qi(_with_world(cfg, days=3), daily_allowance=130)
    run_dir = tmp_path / "tiny"
    summary = run_sim(cfg, run_dir, difftest_interval=1)
    assert summary.days_elapsed == 3

    events = _scan(run_dir)
    actions = [ev for ev in events if ev.kind is EventKind.ACTION]
    degraded = [ev for ev in actions if ev.payload.get("degraded")]
    free = [ev for ev in degraded if ev.payload.get("free")]
    paid = [ev for ev in degraded if not ev.payload.get("free")]
    assert degraded, "an allowance of 130 must force degradation"
    assert free, "the free-rest branch (allowance below rest cost) must be reached"
    assert paid, "paid degraded rests must also occur before the allowance runs dry"

    rest_cost = cfg.qi.action_costs[ActionType.REST]
    for ev in paid:
        assert (ev.payload["type"], ev.qi_delta) == ("rest", -rest_cost)
    for ev in free:
        assert (ev.payload["type"], ev.qi_delta, ev.stones_delta) == ("rest", 0, 0)
        assert ev.payload["free"] is True

    # Independent re-derivation of the allowance cap from the raw log: no
    # agent's within-day spend may ever exceed the allowance.
    spent: defaultdict[str, int] = defaultdict(int)
    for ev in events:
        if ev.kind is EventKind.DAY_STARTED:
            spent.clear()
        elif ev.kind is EventKind.ACTION and ev.qi_delta < 0:
            spent[ev.actor] += -ev.qi_delta
            assert spent[ev.actor] <= cfg.qi.daily_allowance

    assert replay(run_dir).ok


def test_tampered_log_fails_replay(tmp_path):
    cfg = _with_world(load_world_config(CONFIG_PATH), days=2)
    run_dir = tmp_path / "tampered"
    run_sim(cfg, run_dir)
    assert replay(run_dir).ok  # sanity: green before tampering

    _tamper_one_payload(run_dir)
    result = replay(run_dir)
    assert not result.ok
    assert result.mismatches
    assert any("chain" in m for m in result.mismatches)
