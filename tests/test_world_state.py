"""Tests for lamarck.engine.world_state.WorldStateFold: spawn placement,
travel, co-presence, utterance delivery-at-emission with windowing, notes,
reflections, outcomes, and the can_trade advisory matrix.

EventRecords are constructed directly with dummy hashes (the fold never
inspects seq/hash). The delivery contract under test: an utterance is heard
by agents co-located with the speaker AT EMISSION — a listener who travels
away later still heard it; an agent who arrives later never does.
"""

from typing import Any

import pytest

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import EventKind, EventRecord, LiveWorldConfig
from lamarck.engine.world_state import WorldStateFold

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

LOCATIONS = ["meadow", "forge", "spring"]


def _cfg() -> LiveWorldConfig:
    return LiveWorldConfig.model_validate(
        {
            "world": {
                "name": "tiny-live",
                "master_seed": "0xDE5EEDDE5EEDDE5E",
                "days": 10,
                "rounds_per_day": 4,
            },
            "population": {"founders": 8},
            "qi": {"qi_max": 10_000, "daily_allowance": 5_000, "action_costs": _ALL_COSTS},
            "economy": {
                "starting_stones": 20,
                "bounties": [10, 25, 60, 150, 400],
                "materials": [1, 2, 5, 12, 30],
                "stub_success_permille": [500, 250, 120, 50, 15],
            },
            "model": {
                "backend": "scripted",
                "model_id": "test-model",
                "max_tokens": 64,
                "reflection_max_tokens": 48,
                "temp_permille": 700,
                "seed": 1,
                "prompt_budget_chars": 2_000,
            },
            "universe": {"name": "wuxing", "seed": "0x57A57A57A57A57A5", "tiers": 5},
            "live": {"locations": LOCATIONS, "first_discovery_multiplier": 3},
        }
    )


def _ev(
    seq: int,
    kind: EventKind,
    *,
    actor: str = "",
    payload: dict[str, Any] | None = None,
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
        qi_delta=0,
        stones_delta=0,
        hash=_DUMMY_HASH,
    )


def _spawn(seq: int, agent: str, name: str | None = None) -> EventRecord:
    payload: dict[str, Any] = {"agent_id": agent, "qi_max": 10_000, "starting_stones": 20}
    if name is not None:
        payload["name"] = name
    return _ev(seq, EventKind.AGENT_SPAWNED, payload=payload)


def _travel(seq: int, agent: str, to: str, *, day: int = 0, degraded: bool = False) -> EventRecord:
    payload: dict[str, Any] = {"type": "travel", "to": to}
    if degraded:
        payload["degraded"] = True
    return _ev(seq, EventKind.ACTION, actor=agent, payload=payload, day=day)


def _converse(
    seq: int,
    agent: str,
    text: str,
    *,
    day: int = 0,
    tick: int = 0,
    degraded: bool = False,
) -> EventRecord:
    payload: dict[str, Any] = {"type": "converse", "target": "all", "text": text}
    if degraded:
        payload["degraded"] = True
    return _ev(seq, EventKind.ACTION, actor=agent, payload=payload, day=day, tick=tick)


def _note(seq: int, agent: str, text: str, *, degraded: bool = False) -> EventRecord:
    payload: dict[str, Any] = {"type": "note", "text": text}
    if degraded:
        payload["degraded"] = True
    return _ev(seq, EventKind.ACTION, actor=agent, payload=payload)


def _fold(*events: EventRecord) -> WorldStateFold:
    ws = WorldStateFold(_cfg())
    for ev in events:
        ws.apply(ev)
    return ws


# ------------------------------------------------------------ spawn placement


def test_spawn_places_agents_at_first_location() -> None:
    ws = _fold(_spawn(0, "a1", "Ash"), _spawn(1, "a2", "Brook"))
    assert ws.location("a1") == "meadow"
    assert ws.location("a2") == "meadow"


def test_spawn_name_map_with_fallback_to_agent_id() -> None:
    ws = _fold(_spawn(0, "a1", "Ash"), _spawn(1, "a2"))  # a2 has no display name
    assert ws.co_present("a2") == ["Ash"]
    assert ws.co_present("a1") == ["a2"]  # fallback: the id doubles as the name


def test_spawning_twice_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="twice"):
        _fold(_spawn(0, "a1"), _spawn(1, "a1"))


def test_spawn_payload_missing_agent_id_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="agent_id"):
        _fold(_ev(0, EventKind.AGENT_SPAWNED, payload={"qi_max": 1}))


# --------------------------------------------------------------------- travel


def test_travel_moves_only_the_actor() -> None:
    ws = _fold(_spawn(0, "a1"), _spawn(1, "a2"), _travel(2, "a1", "forge"))
    assert ws.location("a1") == "forge"
    assert ws.location("a2") == "meadow"


def test_travel_chain_tracks_latest_location() -> None:
    ws = _fold(_spawn(0, "a1"), _travel(1, "a1", "forge"), _travel(2, "a1", "spring"))
    assert ws.location("a1") == "spring"


def test_travel_to_unknown_location_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="unknown location"):
        _fold(_spawn(0, "a1"), _travel(1, "a1", "volcano"))


def test_travel_without_destination_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="unknown location"):
        _fold(_spawn(0, "a1"), _ev(1, EventKind.ACTION, actor="a1", payload={"type": "travel"}))


def test_degraded_travel_is_ignored() -> None:
    ws = _fold(_spawn(0, "a1"), _travel(1, "a1", "forge", degraded=True))
    assert ws.location("a1") == "meadow"


def test_degraded_action_ignored_even_with_malformed_payload() -> None:
    """Degradation short-circuits BEFORE payload validation: a forfeited slot
    may carry an arbitrary wanted-payload and must never trip the fold."""
    ws = _fold(
        _spawn(0, "a1"),
        _ev(
            1,
            EventKind.ACTION,
            actor="a1",
            payload={"type": "travel", "degraded": True, "reason": "malformed"},  # no "to"
        ),
    )
    assert ws.location("a1") == "meadow"


def test_action_by_unknown_actor_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="unregistered"):
        _fold(_travel(0, "ghost", "forge"))


def test_other_action_types_have_no_world_effect() -> None:
    ws = _fold(
        _spawn(0, "a1"),
        _ev(1, EventKind.ACTION, actor="a1", payload={"type": "experiment", "tier": 2}),
        _ev(2, EventKind.ACTION, actor="a1", payload={"type": "rest"}),
    )
    assert ws.location("a1") == "meadow"
    assert ws.notes("a1") == []
    assert ws.heard("a1", 0) == []


def test_non_worldly_kinds_are_ignored() -> None:
    ws = _fold(
        _ev(0, EventKind.RUN_STARTED),
        _spawn(1, "a1"),
        _ev(2, EventKind.DAY_STARTED),
        _ev(3, EventKind.PHASE_STARTED, payload={"phase": "dawn"}),
        _ev(4, EventKind.LLM_CALL, actor="a1", payload={"purpose": "action"}),
        _ev(5, EventKind.LEDGER_ADJUST, actor="a1", payload={"reason": "bounty"}),
        _ev(6, EventKind.RUN_FINISHED),
    )
    assert ws.location("a1") == "meadow"
    assert ws.co_present("a1") == []


# ---------------------------------------------------------------- co-presence


def test_co_present_lists_living_co_located_others_in_spawn_order() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _spawn(2, "a3", "Cinder"),
        _spawn(3, "a4", "Dew"),
    )
    assert ws.co_present("a3") == ["Ash", "Brook", "Dew"]  # spawn order, self excluded


def test_co_present_excludes_dead_and_departed() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _spawn(2, "a3", "Cinder"),
        _spawn(3, "a4", "Dew"),
        _travel(4, "a2", "forge"),
        _ev(5, EventKind.AGENT_DIED, actor="a3", payload={"cause": "qi_exhausted"}),
    )
    assert ws.co_present("a1") == ["Dew"]
    assert ws.co_present("a2") == []  # alone at the forge
    # The dead keep a location; the living do not see them.
    assert ws.location("a3") == "meadow"


def test_spawn_order_is_kept_after_round_trips() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _spawn(2, "a3", "Cinder"),
        _travel(3, "a1", "forge"),
        _travel(4, "a1", "meadow"),  # round trip must not demote a1's order
    )
    assert ws.co_present("a3") == ["Ash", "Brook"]


def test_dying_twice_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="dead"):
        _fold(
            _spawn(0, "a1"),
            _ev(1, EventKind.AGENT_DIED, actor="a1"),
            _ev(2, EventKind.AGENT_DIED, actor="a1"),
        )


# ------------------------------------------------------- utterance delivery


def test_delivery_at_emission_to_co_located_others_only() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _spawn(2, "a3", "Cinder"),
        _travel(3, "a3", "forge"),
        _converse(4, "a1", "hello meadow"),
    )
    assert [u.text for u in ws.heard("a2", 0)] == ["hello meadow"]
    assert ws.heard("a3", 0) == []  # not co-located at emission
    assert ws.heard("a1", 0) == []  # speakers do not hear themselves
    heard = ws.heard("a2", 0)[0]
    assert heard.from_agent == "a1"
    assert heard.from_name == "Ash"


def test_listener_who_travels_after_emission_still_heard_it() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _converse(2, "a1", "remember this"),
        _travel(3, "a2", "spring"),
    )
    assert [u.text for u in ws.heard("a2", 0)] == ["remember this"]


def test_agent_arriving_after_emission_never_hears_it() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _travel(2, "a2", "forge"),
        _converse(3, "a1", "you missed this"),
        _travel(4, "a2", "meadow"),  # arrives after emission
    )
    assert ws.heard("a2", 0) == []


def test_dead_co_located_agent_receives_nothing() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _ev(2, EventKind.AGENT_DIED, actor="a2"),
        _converse(3, "a1", "eulogy"),
    )
    assert ws.heard("a2", 0) == []


def test_degraded_converse_is_ignored() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _converse(2, "a1", "never said", degraded=True),
    )
    assert ws.heard("a2", 0) == []


def test_converse_without_text_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="text"):
        _fold(
            _spawn(0, "a1"),
            _ev(1, EventKind.ACTION, actor="a1", payload={"type": "converse"}),
        )


def test_heard_window_is_today_and_yesterday() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _converse(2, "a1", "day zero", day=0),
    )
    assert [u.text for u in ws.heard("a2", 0)] == ["day zero"]
    assert [u.text for u in ws.heard("a2", 1)] == ["day zero"]  # yesterday still audible
    assert ws.heard("a2", 2) == []  # two days later it has faded


def test_heard_caps_at_six_most_recent_oldest_first() -> None:
    events = [_spawn(0, "a1", "Ash"), _spawn(1, "a2", "Brook")]
    for i in range(8):
        events.append(_converse(2 + i, "a1", f"u{i}", day=1, tick=i))
    ws = _fold(*events)
    assert [u.text for u in ws.heard("a2", 1)] == ["u2", "u3", "u4", "u5", "u6", "u7"]


def test_window_filter_applies_before_the_cap() -> None:
    """Six stale utterances must not crowd out fresh ones: the day window
    filters first, then the most-recent-6 cap applies."""
    events = [_spawn(0, "a1", "Ash"), _spawn(1, "a2", "Brook")]
    seq = 2
    for i in range(6):
        events.append(_converse(seq, "a1", f"old{i}", day=0, tick=i))
        seq += 1
    events.append(_converse(seq, "a1", "fresh", day=2))
    ws = _fold(*events)
    assert [u.text for u in ws.heard("a2", 3)] == ["fresh"]


# ------------------------------------------------ notes, reflection, outcomes


def test_notes_keep_last_five_oldest_first() -> None:
    events = [_spawn(0, "a1")]
    for i in range(7):
        events.append(_note(1 + i, "a1", f"n{i}"))
    ws = _fold(*events)
    assert ws.notes("a1") == ["n2", "n3", "n4", "n5", "n6"]


def test_degraded_note_is_ignored() -> None:
    ws = _fold(_spawn(0, "a1"), _note(1, "a1", "kept"), _note(2, "a1", "lost", degraded=True))
    assert ws.notes("a1") == ["kept"]


def test_notes_are_private() -> None:
    ws = _fold(_spawn(0, "a1"), _spawn(1, "a2"), _note(2, "a1", "secret"))
    assert ws.notes("a2") == []


def test_reflection_defaults_empty_and_latest_wins() -> None:
    ws = _fold(_spawn(0, "a1"))
    assert ws.reflection("a1") == ""
    ws.apply(_ev(1, EventKind.REFLECTION, actor="a1", payload={"text": "first"}))
    ws.apply(_ev(2, EventKind.REFLECTION, actor="a1", payload={"text": "second"}))
    assert ws.reflection("a1") == "second"


def test_reflection_without_text_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="text"):
        _fold(_spawn(0, "a1"), _ev(1, EventKind.REFLECTION, actor="a1", payload={}))


def test_outcomes_keep_last_three_oldest_first() -> None:
    events = [_spawn(0, "a1")]
    for i in range(4):
        events.append(
            _ev(
                1 + i,
                EventKind.TASK_ATTEMPT,
                actor="a1",
                payload={"message": f"m{i}", "step_products": []},
            )
        )
    ws = _fold(*events)
    assert ws.outcomes("a1") == ["m1", "m2", "m3"]


def test_task_attempt_without_message_asserts() -> None:
    with pytest.raises(LamarckAssertionError, match="message"):
        _fold(_spawn(0, "a1"), _ev(1, EventKind.TASK_ATTEMPT, actor="a1", payload={}))


# ------------------------------------------------------------------ can_trade


def test_can_trade_matrix() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _spawn(2, "a3", "Cinder"),
        _spawn(3, "a4", "Dew"),
        _travel(4, "a3", "forge"),
        _ev(5, EventKind.AGENT_DIED, actor="a4"),
    )
    assert ws.can_trade("a1", "a2") == (True, "")
    ok, reason = ws.can_trade("a1", "a4")
    assert not ok and "dead" in reason
    ok, reason = ws.can_trade("a1", "a3")
    assert not ok and "not here" in reason
    ok, reason = ws.can_trade("a1", "a1")
    assert not ok and "yourself" in reason
    ok, reason = ws.can_trade("a1", "nobody")
    assert not ok and "nobody" in reason


def test_can_trade_never_mutates() -> None:
    ws = _fold(_spawn(0, "a1", "Ash"), _spawn(1, "a2", "Brook"))
    assert ws.can_trade("a1", "a2") == (True, "")
    assert ws.can_trade("a1", "a2") == (True, "")  # repeatable, read-only
    assert ws.co_present("a1") == ["Brook"]


# ------------------------------------------------------------ query hygiene


def test_queries_return_fresh_lists_not_views() -> None:
    ws = _fold(
        _spawn(0, "a1", "Ash"),
        _spawn(1, "a2", "Brook"),
        _converse(2, "a1", "hi"),
        _note(3, "a2", "n"),
        _ev(4, EventKind.TASK_ATTEMPT, actor="a2", payload={"message": "m", "step_products": []}),
    )
    ws.heard("a2", 0).clear()
    ws.notes("a2").clear()
    ws.outcomes("a2").clear()
    ws.co_present("a1").clear()
    assert [u.text for u in ws.heard("a2", 0)] == ["hi"]
    assert ws.notes("a2") == ["n"]
    assert ws.outcomes("a2") == ["m"]
    assert ws.co_present("a1") == ["Brook"]


def test_queries_on_unknown_agent_assert() -> None:
    ws = _fold(_spawn(0, "a1"))
    for query in (
        lambda: ws.location("ghost"),
        lambda: ws.co_present("ghost"),
        lambda: ws.heard("ghost", 0),
        lambda: ws.notes("ghost"),
        lambda: ws.reflection("ghost"),
        lambda: ws.outcomes("ghost"),
        lambda: ws.can_trade("ghost", "a1"),
    ):
        with pytest.raises(LamarckAssertionError, match="unregistered"):
            query()


class TestSatchel:
    def test_satchel_accumulates_non_slag_first_acquired_deduped(self) -> None:
        fold = _fold(
            _spawn(0, "a1"),
            _ev(
                1,
                EventKind.TASK_ATTEMPT,
                actor="a1",
                payload={"message": "m", "step_products": ["iron-ash", "slag", "iron-ash"]},
            ),
            _ev(
                2,
                EventKind.TASK_ATTEMPT,
                actor="a1",
                payload={"message": "m", "step_products": ["pale-dew", "iron-ash"]},
            ),
        )
        assert fold.satchel("a1") == ["iron-ash", "pale-dew"]

    def test_satchel_is_per_agent_and_fresh_copies(self) -> None:
        fold = _fold(
            _spawn(0, "a1"),
            _spawn(1, "a2"),
            _ev(
                2,
                EventKind.TASK_ATTEMPT,
                actor="a1",
                payload={"message": "m", "step_products": ["iron-ash"]},
            ),
        )
        assert fold.satchel("a2") == []
        first = fold.satchel("a1")
        first.append("tampered")
        assert fold.satchel("a1") == ["iron-ash"]
