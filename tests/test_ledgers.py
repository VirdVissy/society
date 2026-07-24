"""Tests for lamarck.engine.ledgers: live ledger, independent fold, difftest,
and the allowance meter.

EventRecords are constructed directly with dummy hashes — the ledger must not
care about hashes or chain integrity (that separation from the event store is
intentional and locked here by using obviously-fake hash values).
"""

import random
from typing import Any

import pytest

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import EventKind, EventRecord, WorldConfig
from lamarck.engine.ledgers import (
    AllowanceMeter,
    Ledgers,
    assert_difftest,
    fold_balances,
)

_DUMMY_HASH = "0" * 64

_ALL_COSTS = {
    "experiment": 800,
    "converse": 600,
    "teach": 900,
    "study": 700,
    "trade": 400,
    "note": 200,
    "travel": 500,
    "meditate": 100,
    "challenge": 900,
    "attempt_breakthrough": 1000,
    "rest": 50,
}


def _cfg(daily_allowance: int = 100) -> WorldConfig:
    return WorldConfig.model_validate(
        {
            "world": {
                "name": "tiny",
                "master_seed": "0xDE5EEDDE5EEDDE5E",
                "days": 10,
                "rounds_per_day": 4,
            },
            "population": {"founders": 8},
            "qi": {
                "qi_max": 10_000,
                "daily_allowance": daily_allowance,
                "action_costs": _ALL_COSTS,
            },
            "economy": {
                "starting_stones": 20,
                "bounties": [10, 25, 60, 150, 400],
                "materials": [1, 2, 5, 12, 30],
                "stub_success_permille": [500, 250, 120, 50, 15],
            },
        }
    )


def _ev(
    seq: int,
    kind: EventKind,
    *,
    actor: str = "",
    payload: dict[str, Any] | None = None,
    qi: int = 0,
    stones: int = 0,
    day: int = 0,
    tick: int = 0,
    hash_: str = _DUMMY_HASH,
) -> EventRecord:
    return EventRecord(
        seq=seq,
        day=day,
        tick=tick,
        kind=kind,
        actor=actor,
        payload=payload or {},
        qi_delta=qi,
        stones_delta=stones,
        hash=hash_,
    )


def _spawn(seq: int, agent: str, qi_max: int = 100, stones: int = 5) -> EventRecord:
    return _ev(
        seq,
        EventKind.AGENT_SPAWNED,
        payload={"agent_id": agent, "qi_max": qi_max, "starting_stones": stones},
    )


# ---------------------------------------------------------------- lifecycle


def test_spawn_registers_starting_balances() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a", qi_max=100, stones=5))
    b = led.balances()
    assert b.qi == {"a": 100}
    assert b.stones == {"a": 5}
    assert b.alive == {"a": True}


def test_full_lifecycle_spawn_action_adjust_death() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a", qi_max=100, stones=5))
    led.apply(_spawn(1, "b", qi_max=200, stones=0))
    led.apply(_ev(2, EventKind.DAY_STARTED, payload={"day": 0}))
    led.apply(_ev(3, EventKind.ACTION, actor="a", qi=-30, payload={"type": "study"}))
    led.apply(_ev(4, EventKind.LEDGER_ADJUST, actor="a", stones=10, payload={"reason": "bounty"}))
    # Hash content is irrelevant to the ledger — deliberately different dummy.
    led.apply(_ev(5, EventKind.ACTION, actor="b", qi=-60, stones=0, hash_="f" * 64))
    led.apply(_ev(6, EventKind.AGENT_DIED, actor="b", payload={"cause": "qi_exhausted"}))
    b = led.balances()
    assert b.qi == {"a": 70, "b": 140}
    assert b.stones == {"a": 15, "b": 0}
    assert b.alive == {"a": True, "b": False}
    # Dead agents stay in the books; they just cannot be targeted any more.
    with pytest.raises(LamarckAssertionError, match="dead"):
        led.apply(_ev(7, EventKind.LEDGER_ADJUST, actor="b", stones=1))


def test_action_costs_stones_for_materials() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a", stones=5))
    led.apply(_ev(1, EventKind.ACTION, actor="a", qi=-80, stones=-2, payload={"tier": 2}))
    assert led.balances().stones == {"a": 3}


# ------------------------------------------------------------------- asserts


def test_action_on_unregistered_agent_asserts() -> None:
    led = Ledgers(_cfg())
    with pytest.raises(LamarckAssertionError, match="unregistered"):
        led.apply(_ev(0, EventKind.ACTION, actor="ghost", qi=-1))


def test_action_on_dead_agent_asserts() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    led.apply(_ev(1, EventKind.AGENT_DIED, actor="a"))
    with pytest.raises(LamarckAssertionError, match="dead"):
        led.apply(_ev(2, EventKind.ACTION, actor="a", qi=-1))


def test_dying_twice_asserts() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    led.apply(_ev(1, EventKind.AGENT_DIED, actor="a"))
    with pytest.raises(LamarckAssertionError, match="dead"):
        led.apply(_ev(2, EventKind.AGENT_DIED, actor="a"))


def test_dying_unregistered_asserts() -> None:
    led = Ledgers(_cfg())
    with pytest.raises(LamarckAssertionError, match="unregistered"):
        led.apply(_ev(0, EventKind.AGENT_DIED, actor="ghost"))


def test_spawning_twice_asserts() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    with pytest.raises(LamarckAssertionError, match="twice"):
        led.apply(_spawn(1, "a"))


def test_stones_never_go_negative_and_state_unchanged_on_failure() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a", qi_max=100, stones=5))
    before = led.balances()
    with pytest.raises(LamarckAssertionError, match="negative"):
        led.apply(_ev(1, EventKind.ACTION, actor="a", qi=-10, stones=-6))
    assert led.balances() == before  # validated before any mutation


def test_qi_may_go_negative() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a", qi_max=30))
    led.apply(_ev(1, EventKind.ACTION, actor="a", qi=-50))
    b = led.balances()
    assert b.qi == {"a": -20}
    assert b.alive == {"a": True}  # death is decided at dusk, not by the ledger


def test_world_spawn_and_death_events_must_carry_zero_deltas() -> None:
    cases = [
        _ev(0, EventKind.DAY_STARTED, qi=1),
        _ev(0, EventKind.RUN_STARTED, stones=1),
        _ev(0, EventKind.PHASE_STARTED, qi=-1),
        _ev(0, EventKind.RUN_FINISHED, stones=-1),
        _ev(
            0,
            EventKind.AGENT_SPAWNED,
            payload={"agent_id": "a", "qi_max": 1, "starting_stones": 0},
            qi=1,
        ),
    ]
    for bad in cases:
        led = Ledgers(_cfg())
        with pytest.raises(LamarckAssertionError, match="zero deltas"):
            led.apply(bad)
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    with pytest.raises(LamarckAssertionError, match="zero deltas"):
        led.apply(_ev(1, EventKind.AGENT_DIED, actor="a", qi=-1))


def test_spawn_payload_is_validated() -> None:
    bad_payloads: list[dict[str, Any]] = [
        {},
        {"agent_id": "", "qi_max": 1, "starting_stones": 0},
        {"agent_id": "a", "starting_stones": 0},
        {"agent_id": "a", "qi_max": "100", "starting_stones": 0},
        {"agent_id": "a", "qi_max": True, "starting_stones": 0},  # bool is not an int here
        {"agent_id": "a", "qi_max": 1, "starting_stones": -1},
    ]
    for payload in bad_payloads:
        led = Ledgers(_cfg())
        with pytest.raises(LamarckAssertionError, match="AGENT_SPAWNED payload"):
            led.apply(_ev(0, EventKind.AGENT_SPAWNED, payload=payload))


def test_noop_kinds_do_not_touch_balances() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    before = led.balances()
    led.apply(_ev(1, EventKind.RUN_STARTED))
    led.apply(_ev(2, EventKind.PHASE_STARTED, payload={"phase": "dawn"}))
    led.apply(_ev(3, EventKind.RUN_FINISHED))
    assert led.balances() == before


# ----------------------------------------------------- per-day spend + meter


def test_per_day_spend_resets_on_day_started() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1000))
    led.apply(_ev(1, EventKind.DAY_STARTED))
    led.apply(_ev(2, EventKind.ACTION, actor="a", qi=-60))
    assert led.allowance.spent("a") == 60
    assert led.allowance.remaining("a") == 40
    led.apply(_ev(3, EventKind.ACTION, actor="a", qi=-40))
    assert led.allowance.spent("a") == 100
    led.apply(_ev(4, EventKind.DAY_STARTED, day=1))
    assert led.allowance.spent("a") == 0
    led.apply(_ev(5, EventKind.ACTION, actor="a", qi=-100, day=1))
    assert led.allowance.spent("a") == 100


def test_committed_over_allowance_action_asserts() -> None:
    """A committed ACTION that busts the daily cap means the runner failed to
    degrade it — always a bug, hence LMK_ASSERT in the ledger-owned meter."""
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1000))
    led.apply(_ev(1, EventKind.ACTION, actor="a", qi=-100))
    with pytest.raises(LamarckAssertionError, match="allowance"):
        led.apply(_ev(2, EventKind.ACTION, actor="a", qi=-1))


def test_only_actions_count_as_spend() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1000))
    led.apply(_ev(1, EventKind.LEDGER_ADJUST, actor="a", qi=-50, payload={"reason": "fine"}))
    assert led.allowance.spent("a") == 0  # adjustments are not action spend
    led.apply(_ev(2, EventKind.ACTION, actor="a", qi=7))  # qi gain: not a spend either
    assert led.allowance.spent("a") == 0


def test_allowance_meter_boundary() -> None:
    meter = AllowanceMeter(100)
    assert meter.can_spend("a", 100)  # exactly the allowance is allowed
    assert not meter.can_spend("a", 101)  # one more is not
    meter.note_spend("a", 100)
    assert meter.can_spend("a", 0)
    assert not meter.can_spend("a", 1)
    assert meter.spent("a") == 100
    assert meter.remaining("a") == 0
    meter.reset_day()
    assert meter.can_spend("a", 100)
    assert meter.spent("a") == 0


def test_allowance_meter_asserts() -> None:
    meter = AllowanceMeter(100)
    with pytest.raises(LamarckAssertionError, match="allowance"):
        meter.note_spend("a", 101)
    with pytest.raises(LamarckAssertionError, match="non-negative"):
        meter.can_spend("a", -1)
    with pytest.raises(LamarckAssertionError, match="non-negative"):
        meter.note_spend("a", -1)
    with pytest.raises(LamarckAssertionError, match="daily_allowance"):
        AllowanceMeter(0)


def test_meter_tracks_agents_independently() -> None:
    meter = AllowanceMeter(100)
    meter.note_spend("a", 100)
    assert meter.can_spend("b", 100)  # b is untouched by a's spending
    assert meter.spent("b") == 0


# ------------------------------------------------------------ fold + difftest


def _synthetic_history(cfg: WorldConfig) -> list[EventRecord]:
    """A seeded ~250-event history mixing every EventKind, valid by
    construction: stones stay non-negative, daily spends stay within the
    allowance, dead agents act no further."""
    rng = random.Random(0xDE5EED)
    events: list[EventRecord] = []
    agents = [f"a{i}" for i in range(8)]
    stones = dict.fromkeys(agents, 20)
    alive = dict.fromkeys(agents, True)
    seq = 0

    def emit(kind: EventKind, **kw: Any) -> None:
        nonlocal seq
        events.append(_ev(seq, kind, **kw))
        seq += 1

    emit(EventKind.RUN_STARTED, payload={"schema_version": 1})
    for a in agents:
        emit(
            EventKind.AGENT_SPAWNED,
            payload={"agent_id": a, "qi_max": 10_000, "starting_stones": 20},
        )
    for day in range(8):
        emit(EventKind.DAY_STARTED, day=day, payload={"day": day})
        emit(EventKind.PHASE_STARTED, day=day, payload={"phase": "dawn"})
        for _rnd in range(5):
            for a in [x for x in agents if alive[x]]:
                cost = rng.choice([50, 100, 200, 400, 800, 1000])
                spend_materials = rng.randrange(1000) < 400 and stones[a] >= 2
                mat = rng.choice([1, 2]) if spend_materials else 0
                emit(
                    EventKind.ACTION,
                    day=day,
                    actor=a,
                    qi=-cost,
                    stones=-mat,
                    payload={"type": "experiment", "tier": 1},
                )
                stones[a] -= mat
                if rng.randrange(1000) < 300:
                    credit = rng.randrange(1, 30)
                    emit(
                        EventKind.LEDGER_ADJUST,
                        day=day,
                        actor=a,
                        stones=credit,
                        payload={"reason": "bounty"},
                    )
                    stones[a] += credit
        still_alive = [x for x in agents if alive[x]]
        if rng.randrange(1000) < 500 and len(still_alive) > 1:
            victim = rng.choice(still_alive)
            emit(EventKind.AGENT_DIED, day=day, actor=victim, payload={"cause": "qi_exhausted"})
            alive[victim] = False
    emit(EventKind.RUN_FINISHED, payload={"days": 8})
    return events


def test_fold_matches_live_on_synthetic_history() -> None:
    cfg = _cfg(daily_allowance=5_000)  # 5 rounds x max cost 1000 fits exactly
    events = _synthetic_history(cfg)
    assert len(events) >= 200
    phase0_kinds = {
        EventKind.RUN_STARTED,
        EventKind.DAY_STARTED,
        EventKind.PHASE_STARTED,
        EventKind.AGENT_SPAWNED,
        EventKind.ACTION,
        EventKind.LEDGER_ADJUST,
        EventKind.AGENT_DIED,
        EventKind.RUN_FINISHED,
    }
    # Phase-0 generator covers the Phase-0 kinds; Phase-1 kinds (llm_call,
    # task_attempt, reflection) are exercised by tests/test_ledgers_p1.py.
    assert {ev.kind for ev in events} == phase0_kinds  # every phase-0 kind exercised
    live = Ledgers(cfg)
    for ev in events:
        live.apply(ev)
    fold = fold_balances(events, cfg)
    assert fold == live.balances()
    assert_difftest(live, events, cfg)  # and the difftest agrees, quietly


def test_fold_matches_live_at_every_dusk_boundary() -> None:
    """The difftest contract holds at any event boundary, not just run end."""
    cfg = _cfg(daily_allowance=5_000)
    events = _synthetic_history(cfg)
    live = Ledgers(cfg)
    for i, ev in enumerate(events):
        live.apply(ev)
        if ev.kind is EventKind.AGENT_DIED or ev.kind is EventKind.DAY_STARTED:
            assert fold_balances(events[: i + 1], cfg) == live.balances()


def test_difftest_catches_corrupted_qi() -> None:
    cfg = _cfg(daily_allowance=5_000)
    events = _synthetic_history(cfg)
    live = Ledgers(cfg)
    for ev in events:
        live.apply(ev)
    live._qi["a3"] += 1  # deliberate corruption of the live ledger
    with pytest.raises(LamarckAssertionError, match="difftest") as excinfo:
        assert_difftest(live, events, cfg)
    msg = str(excinfo.value)
    assert "qi['a3']" in msg
    assert "live=" in msg and "fold=" in msg


def test_difftest_catches_missing_agent() -> None:
    cfg = _cfg(daily_allowance=5_000)
    events = _synthetic_history(cfg)
    live = Ledgers(cfg)
    for ev in events:
        live.apply(ev)
    del live._stones["a5"]  # deliberate corruption: agent vanished from a book
    with pytest.raises(LamarckAssertionError, match="difftest") as excinfo:
        assert_difftest(live, events, cfg)
    assert "stones['a5']" in str(excinfo.value)
    assert "<absent>" in str(excinfo.value)


def test_balances_returns_copies_not_views() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a", qi_max=100, stones=5))
    snap = led.balances()
    snap.qi["a"] = -999
    snap.alive["a"] = False
    fresh = led.balances()
    assert fresh.qi == {"a": 100}
    assert fresh.alive == {"a": True}
