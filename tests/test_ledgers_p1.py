"""Phase-1 ledger tests: LLM_CALL billing, the extended allowance feed
(ACTION + LLM_CALL), the TASK_ATTEMPT/REFLECTION zero-delta no-ops, and a
synthetic history exercising ALL 11 EventKinds through live-vs-fold.

Mirrors tests/test_ledgers.py conventions: EventRecords built directly with
dummy hashes (the ledger must not care about chain integrity), a tiny inline
config, and a seeded valid-by-construction history generator.
"""

import random
from typing import Any

import pytest

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import EventKind, EventRecord, WorldConfig, qi_llm_cost
from lamarck.engine.ledgers import Ledgers, assert_difftest, fold_balances

_DUMMY_HASH = "0" * 64

_ALL_COSTS = {
    "experiment": 200,
    "converse": 0,
    "teach": 100,
    "study": 50,
    "trade": 50,
    "note": 0,
    "travel": 500,
    "meditate": 50,
    "challenge": 100,
    "attempt_breakthrough": 200,
    "rest": 0,
}


def _cfg(daily_allowance: int = 100) -> WorldConfig:
    return WorldConfig.model_validate(
        {
            "world": {
                "name": "tiny-live",
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
        hash=_DUMMY_HASH,
    )


def _spawn(seq: int, agent: str, qi_max: int = 100, stones: int = 5) -> EventRecord:
    return _ev(
        seq,
        EventKind.AGENT_SPAWNED,
        payload={"agent_id": agent, "qi_max": qi_max, "starting_stones": stones},
    )


def _llm(
    seq: int, agent: str, usage_in: int, usage_out: int, *, day: int = 0, stones: int = 0
) -> EventRecord:
    return _ev(
        seq,
        EventKind.LLM_CALL,
        actor=agent,
        qi=-qi_llm_cost(usage_in, usage_out),
        stones=stones,
        day=day,
        payload={"usage_in": usage_in, "usage_out": usage_out},
    )


# ------------------------------------------------------------ LLM_CALL billing


@pytest.mark.parametrize(
    ("usage_in", "usage_out", "expected_cost"),
    [
        (0, 0, 0),  # a free call is billable at zero (and is not spend)
        (1, 0, 1),  # ceil(1/4) = 1
        (3, 0, 1),  # ceil(3/4) = 1
        (4, 0, 1),  # exact divisor boundary
        (5, 0, 2),  # ceil(5/4) = 2
        (0, 7, 7),  # output tokens bill 1:1
        (8, 3, 5),  # 2 + 3
        (100, 7, 32),  # 25 + 7
        (999, 321, 571),  # ceil(999/4) = 250
        (1000, 0, 250),
    ],
)
def test_llm_call_bills_qi_llm_cost_vectors(
    usage_in: int, usage_out: int, expected_cost: int
) -> None:
    assert qi_llm_cost(usage_in, usage_out) == expected_cost
    led = Ledgers(_cfg(daily_allowance=1_000))
    led.apply(_spawn(0, "a", qi_max=5_000))
    led.apply(_llm(1, "a", usage_in, usage_out))
    b = led.balances()
    assert b.qi == {"a": 5_000 - expected_cost}
    assert b.stones == {"a": 5}  # LLM_CALL never moves stones
    assert led.allowance.spent("a") == expected_cost


def test_llm_call_qi_may_go_negative() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=30))
    led.apply(_llm(1, "a", 0, 50))  # costs 50 > 30 qi on hand
    b = led.balances()
    assert b.qi == {"a": -20}
    assert b.alive == {"a": True}  # death is decided at dusk, not by the ledger


def test_llm_call_nonzero_stones_asserts() -> None:
    for bad_stones in (1, -1):
        led = Ledgers(_cfg())
        led.apply(_spawn(0, "a"))
        with pytest.raises(LamarckAssertionError, match="stones_delta"):
            led.apply(_llm(1, "a", 4, 4, stones=bad_stones))


def test_llm_call_on_unregistered_agent_asserts() -> None:
    led = Ledgers(_cfg())
    with pytest.raises(LamarckAssertionError, match="unregistered"):
        led.apply(_llm(0, "ghost", 4, 4))


def test_llm_call_on_dead_agent_asserts() -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    led.apply(_ev(1, EventKind.AGENT_DIED, actor="a"))
    with pytest.raises(LamarckAssertionError, match="dead"):
        led.apply(_llm(2, "a", 4, 4))


# ------------------------------------------- allowance counts ACTION + LLM_CALL


def test_allowance_counts_action_plus_llm_call_exactly_at_cap() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1_000))
    led.apply(_ev(1, EventKind.ACTION, actor="a", qi=-60, payload={"type": "study"}))
    led.apply(_llm(2, "a", 0, 40))  # 60 + 40 == cap: allowed
    assert led.allowance.spent("a") == 100
    assert led.allowance.remaining("a") == 0


def test_allowance_one_over_cap_via_llm_call_asserts() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1_000))
    led.apply(_ev(1, EventKind.ACTION, actor="a", qi=-60, payload={"type": "study"}))
    led.apply(_llm(2, "a", 0, 40))
    with pytest.raises(LamarckAssertionError, match="allowance"):
        led.apply(_llm(3, "a", 0, 1))  # +1 over the cap


def test_allowance_one_over_cap_via_action_after_llm_calls_asserts() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1_000))
    led.apply(_llm(1, "a", 0, 100))  # LLM alone may hit the cap exactly
    assert led.allowance.spent("a") == 100
    with pytest.raises(LamarckAssertionError, match="allowance"):
        led.apply(_ev(2, EventKind.ACTION, actor="a", qi=-1, payload={"type": "rest"}))


def test_day_started_resets_llm_spend_too() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1_000))
    led.apply(_llm(1, "a", 0, 100))
    assert led.allowance.spent("a") == 100
    led.apply(_ev(2, EventKind.DAY_STARTED, day=1))
    assert led.allowance.spent("a") == 0
    led.apply(_llm(3, "a", 0, 100, day=1))  # a fresh full allowance
    assert led.allowance.spent("a") == 100


def test_zero_cost_llm_call_is_not_spend() -> None:
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a"))
    led.apply(_llm(1, "a", 0, 0))
    assert led.allowance.spent("a") == 0


def test_ledger_adjust_still_does_not_count_as_spend() -> None:
    """Phase-0 rule unchanged by the Phase-1 extension: only ACTION and
    LLM_CALL feed the allowance."""
    led = Ledgers(_cfg(daily_allowance=100))
    led.apply(_spawn(0, "a", qi_max=1_000))
    led.apply(_ev(1, EventKind.LEDGER_ADJUST, actor="a", qi=-500, payload={"reason": "fine"}))
    assert led.allowance.spent("a") == 0


# ---------------------------------------- TASK_ATTEMPT / REFLECTION no-ops


@pytest.mark.parametrize("kind", [EventKind.TASK_ATTEMPT, EventKind.REFLECTION])
def test_task_attempt_and_reflection_must_carry_zero_deltas(kind: EventKind) -> None:
    for bad in (
        {"qi": 1},
        {"qi": -1},
        {"stones": 1},
        {"stones": -1},
    ):
        led = Ledgers(_cfg())
        led.apply(_spawn(0, "a"))
        with pytest.raises(LamarckAssertionError, match="zero deltas"):
            led.apply(_ev(1, kind, actor="a", **bad))


@pytest.mark.parametrize("kind", [EventKind.TASK_ATTEMPT, EventKind.REFLECTION])
def test_task_attempt_and_reflection_require_registered_alive_actor(kind: EventKind) -> None:
    led = Ledgers(_cfg())
    with pytest.raises(LamarckAssertionError, match="unregistered"):
        led.apply(_ev(0, kind, actor="ghost"))
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    led.apply(_ev(1, EventKind.AGENT_DIED, actor="a"))
    with pytest.raises(LamarckAssertionError, match="dead"):
        led.apply(_ev(2, kind, actor="a"))


@pytest.mark.parametrize("kind", [EventKind.TASK_ATTEMPT, EventKind.REFLECTION])
def test_task_attempt_and_reflection_do_not_touch_balances(kind: EventKind) -> None:
    led = Ledgers(_cfg())
    led.apply(_spawn(0, "a"))
    before = led.balances()
    led.apply(_ev(1, kind, actor="a", payload={"text": "x", "message": "y"}))
    assert led.balances() == before
    assert led.allowance.spent("a") == 0


# ----------------------------------------- synthetic history v2 (all 11 kinds)


def _synthetic_history_v2(cfg: WorldConfig) -> list[EventRecord]:
    """A seeded 300+-event history mixing ALL 11 EventKinds, valid by
    construction: stones stay non-negative, per-day spends (ACTION and
    LLM_CALL negative qi) stay within the allowance, dead agents act no
    further, LLM_CALL/TASK_ATTEMPT/REFLECTION honor their delta rules."""
    rng = random.Random(0xDE5EED1)
    events: list[EventRecord] = []
    agents = [f"a{i}" for i in range(8)]
    stones = dict.fromkeys(agents, 20)
    alive = dict.fromkeys(agents, True)
    allowance = cfg.qi.daily_allowance
    spent = dict.fromkeys(agents, 0)
    seq = 0

    def emit(kind: EventKind, **kw: Any) -> None:
        nonlocal seq
        events.append(_ev(seq, kind, **kw))
        seq += 1

    def emit_llm(a: str, day: int, purpose: str) -> None:
        usage_in = rng.randrange(20, 401)
        usage_out = rng.randrange(5, 61)
        cost = qi_llm_cost(usage_in, usage_out)
        assert spent[a] + cost <= allowance, "generator bug: over-allowance llm call"
        emit(
            EventKind.LLM_CALL,
            day=day,
            actor=a,
            qi=-cost,
            payload={"purpose": purpose, "usage_in": usage_in, "usage_out": usage_out},
        )
        spent[a] += cost

    emit(EventKind.RUN_STARTED, payload={"schema_version": 1})
    for a in agents:
        emit(
            EventKind.AGENT_SPAWNED,
            payload={"agent_id": a, "qi_max": 50_000, "starting_stones": 20, "name": a.upper()},
        )
    for day in range(8):
        emit(EventKind.DAY_STARTED, day=day, payload={"day": day})
        spent = dict.fromkeys(agents, 0)
        emit(EventKind.PHASE_STARTED, day=day, payload={"phase": "dawn"})
        for _rnd in range(5):
            for a in [x for x in agents if alive[x]]:
                for _retry in range(rng.choice([1, 1, 1, 2])):  # retries bill too
                    emit_llm(a, day, "action")
                surcharge = rng.choice([0, 50, 100, 200, 500])
                assert spent[a] + surcharge <= allowance, "generator bug: over-allowance action"
                experiment = rng.randrange(1000) < 350 and stones[a] >= 2
                mat = rng.choice([1, 2]) if experiment else 0
                emit(
                    EventKind.ACTION,
                    day=day,
                    actor=a,
                    qi=-surcharge,
                    stones=-mat,
                    payload=(
                        {"type": "experiment", "tier": 1} if experiment else {"type": "meditate"}
                    ),
                )
                spent[a] += surcharge
                stones[a] -= mat
                if experiment:
                    emit(
                        EventKind.TASK_ATTEMPT,
                        day=day,
                        actor=a,
                        payload={"message": f"attempt-{seq}", "verified": bool(rng.randrange(2))},
                    )
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
        emit(EventKind.PHASE_STARTED, day=day, payload={"phase": "dusk"})
        for a in [x for x in agents if alive[x]]:
            emit_llm(a, day, "reflection")
            emit(EventKind.REFLECTION, day=day, actor=a, payload={"text": f"day {day} thoughts"})
        still_alive = [x for x in agents if alive[x]]
        if rng.randrange(1000) < 400 and len(still_alive) > 1:
            victim = rng.choice(still_alive)
            emit(EventKind.AGENT_DIED, day=day, actor=victim, payload={"cause": "qi_exhausted"})
            alive[victim] = False
    emit(EventKind.RUN_FINISHED, payload={"days": 8})
    return events


def test_fold_matches_live_on_synthetic_history_v2() -> None:
    # 5 rounds x (2 llm calls <= 161 each + surcharge <= 500) + dusk llm <= 161
    cfg = _cfg(daily_allowance=5_000)
    events = _synthetic_history_v2(cfg)
    assert len(events) >= 300
    assert {ev.kind for ev in events} == set(EventKind)  # ALL 11 kinds exercised
    live = Ledgers(cfg)
    for ev in events:
        live.apply(ev)
    fold = fold_balances(events, cfg)
    assert fold == live.balances()
    assert_difftest(live, events, cfg)  # and the difftest agrees, quietly


def test_fold_matches_live_at_boundaries_v2() -> None:
    """The difftest contract holds at any event boundary with the Phase-1
    kinds in the stream, not just at run end."""
    cfg = _cfg(daily_allowance=5_000)
    events = _synthetic_history_v2(cfg)
    live = Ledgers(cfg)
    for i, ev in enumerate(events):
        live.apply(ev)
        if ev.kind is EventKind.AGENT_DIED or ev.kind is EventKind.DAY_STARTED:
            assert fold_balances(events[: i + 1], cfg) == live.balances()


def test_difftest_catches_corruption_with_new_kinds_present() -> None:
    cfg = _cfg(daily_allowance=5_000)
    events = _synthetic_history_v2(cfg)
    live = Ledgers(cfg)
    for ev in events:
        live.apply(ev)
    live._qi["a4"] -= 3  # deliberate corruption of the live ledger
    with pytest.raises(LamarckAssertionError, match="difftest") as excinfo:
        assert_difftest(live, events, cfg)
    msg = str(excinfo.value)
    assert "qi['a4']" in msg
    assert "live=" in msg and "fold=" in msg
