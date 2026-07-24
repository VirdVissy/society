"""Format-locks on the frozen contracts. If one of these fails, a contract
changed — that is allowed only at an integration boundary, never mid-wave."""

from lamarck.contracts import (
    GENESIS_HASH,
    MASTER_SEED_DEFAULT,
    SCHEMA_VERSION,
    WORLD_ACTOR,
    ActionType,
    DayPhase,
    EventDraft,
    EventKind,
)


def test_constants_pinned() -> None:
    assert SCHEMA_VERSION == 1
    assert GENESIS_HASH == "0" * 64
    assert MASTER_SEED_DEFAULT == 0xDE5EEDDE5EEDDE5E
    assert WORLD_ACTOR == ""


def test_event_kinds_pinned() -> None:
    # Phase-1 kinds are APPENDED (integration-boundary change, devlog 002);
    # existing kind strings never change, so Phase-0 hashes are unaffected.
    assert [k.value for k in EventKind] == [
        "run_started",
        "day_started",
        "phase_started",
        "agent_spawned",
        "action",
        "ledger_adjust",
        "agent_died",
        "run_finished",
        "llm_call",
        "task_attempt",
        "reflection",
    ]


def test_action_types_pinned() -> None:
    assert [a.value for a in ActionType] == [
        "experiment",
        "converse",
        "teach",
        "study",
        "trade",
        "note",
        "travel",
        "meditate",
        "challenge",
        "attempt_breakthrough",
        "rest",
    ]


def test_day_phases_pinned() -> None:
    assert [p.value for p in DayPhase] == ["dawn", "action", "dusk", "night"]


def test_event_draft_defaults() -> None:
    d = EventDraft(day=0, tick=0, kind=EventKind.DAY_STARTED)
    assert d.actor == WORLD_ACTOR
    assert d.qi_delta == 0 and d.stones_delta == 0 and d.payload == {}


def test_phase1_constants_pinned() -> None:
    from lamarck.contracts import (
        MAX_ACTION_PARSE_RETRIES,
        NOTE_MAX_CHARS,
        QI_INPUT_DIVISOR,
        qi_llm_cost,
    )

    assert QI_INPUT_DIVISOR == 4
    assert MAX_ACTION_PARSE_RETRIES == 1
    assert NOTE_MAX_CHARS == 500
    # qi_llm_cost = ceil(in/4) + out, pinned by vectors
    assert qi_llm_cost(0, 0) == 0
    assert qi_llm_cost(1, 0) == 1
    assert qi_llm_cost(4, 0) == 1
    assert qi_llm_cost(5, 0) == 2
    assert qi_llm_cost(1500, 150) == 525
    assert qi_llm_cost(7, 3) == 5
