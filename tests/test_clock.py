"""Tests for lamarck.engine.clock.Scheduler.

The pinned sequences below are golden values under the house master seed
0xDE5EEDDE5EEDDE5E; they lock the slot layout AND the exact shuffle stream
consumption. If a pin breaks, scheduling determinism broke — fix the code,
never the pin (unless the contract itself moved at an integration boundary).
"""

import pytest

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import WORLD_ACTOR, DayPhase, TickSlot, WorldConfig
from lamarck.engine.clock import Scheduler
from lamarck.engine.rng import RngStreams

HOUSE_SEED_HEX = "0xDE5EEDDE5EEDDE5E"

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


def _cfg(rounds_per_day: int = 2, ticks_per_agent_per_round: int = 1) -> WorldConfig:
    return WorldConfig.model_validate(
        {
            "world": {
                "name": "tiny",
                "master_seed": HOUSE_SEED_HEX,
                "days": 1,
                "rounds_per_day": rounds_per_day,
                "ticks_per_agent_per_round": ticks_per_agent_per_round,
            },
            "population": {"founders": 2},
            "qi": {"qi_max": 100, "daily_allowance": 10, "action_costs": _ALL_COSTS},
            "economy": {
                "starting_stones": 5,
                "bounties": [1, 2, 3, 4, 5],
                "materials": [1, 2, 3, 4, 5],
                "stub_success_permille": [500, 250, 120, 50, 15],
            },
        }
    )


def _scheduler(rounds: int = 2, ticks: int = 1) -> Scheduler:
    cfg = _cfg(rounds, ticks)
    return Scheduler(cfg, RngStreams(cfg.world.seed_int()))


def _round_orders(slots: list[TickSlot]) -> dict[int, list[str]]:
    """Map round -> actor order of its ACTION slots (in yield order)."""
    orders: dict[int, list[str]] = {}
    for phase, rnd, _tick, actor in slots:
        if phase is DayPhase.ACTION:
            orders.setdefault(rnd, []).append(actor)
    return orders


# ------------------------------------------------------------------- pinned


def test_pinned_slot_sequence_two_agents_two_rounds() -> None:
    """Golden day-0 sequence: 2 agents, 2 rounds, ticks=1, house seed."""
    slots = list(_scheduler().iter_day(0, ["a", "b"]))
    assert slots == [
        (DayPhase.DAWN, 0, 0, ""),
        (DayPhase.ACTION, 0, 1, "b"),
        (DayPhase.ACTION, 0, 2, "a"),
        (DayPhase.ACTION, 1, 3, "a"),
        (DayPhase.ACTION, 1, 4, "b"),
        (DayPhase.DUSK, 0, 5, ""),
        (DayPhase.NIGHT, 0, 6, ""),
    ]


def test_pinned_second_day_continues_the_stream() -> None:
    """The scheduler stream is stateful across days (by design): day 1 on the
    same scheduler consumes the stream where day 0 left it."""
    sched = _scheduler()
    day0 = list(sched.iter_day(0, ["a", "b"]))
    day1 = list(sched.iter_day(1, ["a", "b"]))
    assert _round_orders(day0) == {0: ["b", "a"], 1: ["a", "b"]}
    assert _round_orders(day1) == {0: ["a", "b"], 1: ["b", "a"]}


def test_pinned_grouping_with_two_ticks_per_agent() -> None:
    """ticks_per_agent_per_round=2: each agent's ticks are consecutive, one
    shuffle per round (same round orders as the ticks=1 pin above)."""
    slots = list(_scheduler(rounds=2, ticks=2).iter_day(0, ["a", "b"]))
    assert slots == [
        (DayPhase.DAWN, 0, 0, ""),
        (DayPhase.ACTION, 0, 1, "b"),
        (DayPhase.ACTION, 0, 2, "b"),
        (DayPhase.ACTION, 0, 3, "a"),
        (DayPhase.ACTION, 0, 4, "a"),
        (DayPhase.ACTION, 1, 5, "a"),
        (DayPhase.ACTION, 1, 6, "a"),
        (DayPhase.ACTION, 1, 7, "b"),
        (DayPhase.ACTION, 1, 8, "b"),
        (DayPhase.DUSK, 0, 9, ""),
        (DayPhase.NIGHT, 0, 10, ""),
    ]


# ------------------------------------------------------------------- layout


def test_tick_contiguity_and_phase_placement() -> None:
    slots = list(_scheduler(rounds=3, ticks=2).iter_day(0, ["a", "b", "c"]))
    ticks = [t for _, _, t, _ in slots]
    assert ticks == list(range(len(slots)))  # contiguous from 0
    assert slots[0][0] is DayPhase.DAWN
    assert slots[-2][0] is DayPhase.DUSK
    assert slots[-1][0] is DayPhase.NIGHT
    body = slots[1:-2]
    assert all(phase is DayPhase.ACTION for phase, _, _, _ in body)
    # World slots: actor WORLD_ACTOR, round 0.
    for i in (0, -2, -1):
        assert slots[i][3] == WORLD_ACTOR
        assert slots[i][1] == 0
    # Action slots: rounds non-decreasing, each round has agents*ticks slots,
    # each agent exactly ticks times per round.
    orders = _round_orders(slots)
    assert sorted(orders) == [0, 1, 2]
    for order in orders.values():
        assert len(order) == 6
        assert sorted(order) == ["a", "a", "b", "b", "c", "c"]
    rounds_seen = [rnd for phase, rnd, _, _ in body]
    assert rounds_seen == sorted(rounds_seen)


def test_empty_alive_yields_only_world_slots() -> None:
    slots = list(_scheduler().iter_day(0, []))
    assert slots == [
        (DayPhase.DAWN, 0, 0, ""),
        (DayPhase.DUSK, 0, 1, ""),
        (DayPhase.NIGHT, 0, 2, ""),
    ]


def test_total_slots_per_day() -> None:
    assert _scheduler(rounds=2, ticks=1).total_slots_per_day(2) == 3 + 2 * 2 * 1
    assert _scheduler(rounds=8, ticks=1).total_slots_per_day(8) == 3 + 8 * 8 * 1
    assert _scheduler(rounds=3, ticks=4).total_slots_per_day(5) == 3 + 3 * 5 * 4
    assert _scheduler().total_slots_per_day(0) == 3
    for rounds, ticks, agents in [(2, 1, 2), (3, 2, 3), (1, 3, 4)]:
        sched = _scheduler(rounds, ticks)
        ids = [f"a{i}" for i in range(agents)]
        assert len(list(sched.iter_day(0, ids))) == sched.total_slots_per_day(agents)


# -------------------------------------------------------------- determinism


def test_determinism_across_fresh_instances() -> None:
    """Same seed + same (day, alive_ids) call sequence -> identical slots,
    including after the alive set shrinks mid-run."""
    calls: list[tuple[int, list[str]]] = [
        (0, ["a", "b", "c", "d"]),
        (1, ["a", "b", "c", "d"]),
        (2, ["a", "c", "d"]),  # b died at dusk of day 1
        (3, ["c"]),
    ]
    s1, s2 = _scheduler(), _scheduler()
    for day, alive in calls:
        assert list(s1.iter_day(day, list(alive))) == list(s2.iter_day(day, list(alive)))


def test_reshuffle_varies_across_rounds_and_days() -> None:
    """Over 50 days with 4 agents, the per-round order is reshuffled: many
    distinct orders must appear (>= 2 guards the property; the pinned seed
    actually reaches all 24 permutations)."""
    sched = _scheduler(rounds=2)
    seen: set[tuple[str, ...]] = set()
    for day in range(50):
        slots = list(sched.iter_day(day, ["a", "b", "c", "d"]))
        for order in _round_orders(slots).values():
            seen.add(tuple(order))
    assert len(seen) >= 2
    assert len(seen) == 24  # pinned under the house seed: every permutation


def test_alive_ids_list_is_not_mutated() -> None:
    ids = ["a", "b", "c"]
    list(_scheduler().iter_day(0, ids))
    assert ids == ["a", "b", "c"]


def test_stream_consumed_at_call_time_not_iteration_time() -> None:
    """Two identical schedulers, one iterated lazily, one fully: the NEXT
    day's sequence must match either way (iter_day precomputes eagerly)."""
    s1, s2 = _scheduler(), _scheduler()
    it = s1.iter_day(0, ["a", "b"])  # never advanced
    next(it)  # advance one slot only; stream already fully consumed for day 0
    list(s2.iter_day(0, ["a", "b"]))  # fully consumed
    assert list(s1.iter_day(1, ["a", "b"])) == list(s2.iter_day(1, ["a", "b"]))


# ------------------------------------------------------------------- asserts


def test_iter_day_rejects_bad_inputs() -> None:
    sched = _scheduler()
    with pytest.raises(LamarckAssertionError):
        sched.iter_day(-1, ["a"])
    with pytest.raises(LamarckAssertionError):
        sched.iter_day(0, ["a", "a"])  # duplicate ids
    with pytest.raises(LamarckAssertionError):
        sched.iter_day(0, ["a", WORLD_ACTOR])  # world actor is not an agent
    with pytest.raises(LamarckAssertionError):
        sched.total_slots_per_day(-1)
