"""Tests for lamarck.mind.parser — the action protocol's gatekeeper.

Exhaustive valid/invalid matrix over all 11 actions, plus extraction edge
cases (fences, prose, trailing garbage, nested braces) and the locked
failure-reason catalog. A reason-string change here is a protocol change:
the retry message shows reasons to the model verbatim.
"""

from __future__ import annotations

import json

import pytest

from lamarck.contracts import NOTE_MAX_CHARS, ActionType
from lamarck.mind.parser import ParseFailure, parse_action, retry_message

# ------------------------------------------------------------- valid matrix

VALID_CASES: list[tuple[str, str, ActionType, dict[str, object]]] = [
    (
        "experiment",
        '{"action": "experiment", "task_id": "w1-01", "steps": [["cinnabar", "ash"]]}',
        ActionType.EXPERIMENT,
        {"task_id": "w1-01", "steps": [["cinnabar", "ash"]]},
    ),
    (
        "experiment-multi-step",
        '{"action": "experiment", "task_id": "w3-02", '
        '"steps": [["a", "b"], ["ab", "c"], ["abc", "d"]]}',
        ActionType.EXPERIMENT,
        {"task_id": "w3-02", "steps": [["a", "b"], ["ab", "c"], ["abc", "d"]]},
    ),
    (
        "experiment-empty-steps",  # structurally valid; the universe judges semantics
        '{"action": "experiment", "task_id": "w1-01", "steps": []}',
        ActionType.EXPERIMENT,
        {"task_id": "w1-01", "steps": []},
    ),
    (
        "converse",
        '{"action": "converse", "target": "Bo Shan", "text": "The ash matters."}',
        ActionType.CONVERSE,
        {"target": "Bo Shan", "text": "The ash matters."},
    ),
    ("teach", '{"action": "teach"}', ActionType.TEACH, {}),
    ("study", '{"action": "study"}', ActionType.STUDY, {}),
    (
        "trade",
        '{"action": "trade", "target": "Mei Lin", "stones": 3}',
        ActionType.TRADE,
        {"target": "Mei Lin", "stones": 3},
    ),
    (
        "note",
        '{"action": "note", "text": "Cinnabar plus ash gave slag."}',
        ActionType.NOTE,
        {"text": "Cinnabar plus ash gave slag."},
    ),
    (
        "note-at-limit",
        json.dumps({"action": "note", "text": "x" * NOTE_MAX_CHARS}),
        ActionType.NOTE,
        {"text": "x" * NOTE_MAX_CHARS},
    ),
    (
        "travel",
        '{"action": "travel", "to": "cold-spring"}',
        ActionType.TRAVEL,
        {"to": "cold-spring"},
    ),
    ("meditate", '{"action": "meditate"}', ActionType.MEDITATE, {}),
    ("challenge", '{"action": "challenge"}', ActionType.CHALLENGE, {}),
    (
        "attempt_breakthrough",
        '{"action": "attempt_breakthrough"}',
        ActionType.ATTEMPT_BREAKTHROUGH,
        {},
    ),
    ("rest", '{"action": "rest"}', ActionType.REST, {}),
    # -------------------------------------------------- extraction tolerance
    (
        "fenced-json",
        '```json\n{"action": "rest"}\n```',
        ActionType.REST,
        {},
    ),
    (
        "fenced-no-lang",
        '```\n{"action": "travel", "to": "meadow"}\n```',
        ActionType.TRAVEL,
        {"to": "meadow"},
    ),
    (
        "leading-prose",
        'I have considered the board carefully.\n{"action": "rest"}',
        ActionType.REST,
        {},
    ),
    (
        "trailing-garbage",
        '{"action": "rest"} — and so I settle down for the night.',
        ActionType.REST,
        {},
    ),
    (
        "prose-both-sides",
        'Very well. {"action": "study"} That is my choice.',
        ActionType.STUDY,
        {},
    ),
    (
        "nested-braces-in-strings",
        '{"action": "note", "text": "recipe: {ash} + {ore} -> ??? do not close } early"}',
        ActionType.NOTE,
        {"text": "recipe: {ash} + {ore} -> ??? do not close } early"},
    ),
    (
        "escaped-quote-in-string",
        '{"action": "converse", "target": "Bo Shan", "text": "he said \\"wait\\" {softly}"}',
        ActionType.CONVERSE,
        {"target": "Bo Shan", "text": 'he said "wait" {softly}'},
    ),
    (
        "second-object-ignored",
        '{"action": "rest"} {"action": "travel", "to": "meadow"}',
        ActionType.REST,
        {},
    ),
    (
        "multiline-whitespace",
        '{\n  "action": "trade",\n  "target": "Yan Hua",\n  "stones": 12\n}',
        ActionType.TRADE,
        {"target": "Yan Hua", "stones": 12},
    ),
    (
        "unicode-text",
        '{"action": "note", "text": "丹砂 + 灰 → 渣"}',
        ActionType.NOTE,
        {"text": "丹砂 + 灰 → 渣"},
    ),
]


@pytest.mark.parametrize(
    ("reply", "action", "args"),
    [case[1:] for case in VALID_CASES],
    ids=[case[0] for case in VALID_CASES],
)
def test_valid(reply: str, action: ActionType, args: dict[str, object]) -> None:
    assert parse_action(reply) == (action, args)


# ----------------------------------------------------------- invalid matrix

INVALID_CASES: list[tuple[str, str, str]] = [
    ("empty", "", "no JSON object found"),
    ("whitespace-only", "   \n\t  ", "no JSON object found"),
    ("prose-no-json", "I will rest today and think.", "no JSON object found"),
    ("unterminated", '{"action": "rest"', "unterminated JSON object"),
    (
        "unterminated-string-brace",
        '{"action": "note", "text": "open } ',
        "unterminated JSON object",
    ),
    ("braces-not-json", "{not json at all}", "not JSON:"),
    ("prose-braces-before-json", 'I think {maybe}. {"action": "rest"}', "not JSON:"),
    ("single-quotes", "{'action': 'rest'}", "not JSON:"),
    ("trailing-comma", '{"action": "rest",}', "not JSON:"),
    # ------------------------------------------------------ no-floats rule
    (
        "float-stones",
        '{"action": "trade", "target": "x", "stones": 2.5}',
        "floats are not allowed",
    ),
    (
        "integral-float",
        '{"action": "trade", "target": "x", "stones": 1.0}',
        "floats are not allowed",
    ),
    (
        "exponent-number",
        '{"action": "trade", "target": "x", "stones": 1e2}',
        "floats are not allowed",
    ),
    (
        "infinity",
        '{"action": "trade", "target": "x", "stones": Infinity}',
        "floats are not allowed",
    ),
    (
        "nan-in-steps",
        '{"action": "experiment", "task_id": "t", "steps": [NaN]}',
        "floats are not allowed",
    ),
    # ------------------------------------------------------- action field
    ("missing-action", '{"task_id": "w1-01"}', 'missing "action" key'),
    ("action-not-string", '{"action": 7}', "action must be a string"),
    ("unknown-action", '{"action": "fly"}', "unknown action 'fly'"),
    ("case-sensitive-action", '{"action": "Rest"}', "unknown action 'Rest'"),
    # ------------------------------------------------------- key discipline
    ("unknown-key-rest", '{"action": "rest", "reason": "tired"}', "unknown key 'reason' for rest"),
    (
        "unknown-key-converse",
        '{"action": "converse", "target": "x", "text": "y", "foo": 1}',
        "unknown key 'foo' for converse",
    ),
    (
        "unknown-key-meditate",
        '{"action": "meditate", "duration": 3}',
        "unknown key 'duration' for meditate",
    ),
    (
        "missing-key-converse",
        '{"action": "converse", "text": "y"}',
        "missing key 'target' for converse",
    ),
    ("missing-key-travel", '{"action": "travel"}', "missing key 'to' for travel"),
    (
        "missing-key-experiment",
        '{"action": "experiment", "task_id": "w1-01"}',
        "missing key 'steps' for experiment",
    ),
    ("missing-key-trade", '{"action": "trade", "target": "x"}', "missing key 'stones' for trade"),
    # ---------------------------------------------------------- field types
    (
        "task-id-not-string",
        '{"action": "experiment", "task_id": 5, "steps": []}',
        "experiment.task_id must be a string",
    ),
    (
        "converse-text-not-string",
        '{"action": "converse", "target": "x", "text": 3}',
        "converse.text must be a string",
    ),
    ("travel-to-not-string", '{"action": "travel", "to": 2}', "travel.to must be a string"),
    ("note-text-not-string", '{"action": "note", "text": ["a"]}', "note.text must be a string"),
    (
        "stones-string",
        '{"action": "trade", "target": "x", "stones": "5"}',
        "trade.stones must be an integer",
    ),
    (
        "stones-bool",
        '{"action": "trade", "target": "x", "stones": true}',
        "trade.stones must be an integer",
    ),
    (
        "stones-null",
        '{"action": "trade", "target": "x", "stones": null}',
        "trade.stones must be an integer",
    ),
    ("stones-zero", '{"action": "trade", "target": "x", "stones": 0}', "trade.stones must be > 0"),
    (
        "stones-negative",
        '{"action": "trade", "target": "x", "stones": -3}',
        "trade.stones must be > 0",
    ),
    # --------------------------------------------------------- steps shape
    (
        "steps-not-list",
        '{"action": "experiment", "task_id": "t", "steps": "ab"}',
        "experiment.steps must be a list of [a, b] pairs",
    ),
    (
        "steps-inner-not-list",
        '{"action": "experiment", "task_id": "t", "steps": ["ab"]}',
        "experiment.steps must be a list of [a, b] pairs",
    ),
    (
        "steps-pair-too-short",
        '{"action": "experiment", "task_id": "t", "steps": [["a"]]}',
        "experiment.steps must be a list of [a, b] pairs",
    ),
    (
        "steps-pair-too-long",
        '{"action": "experiment", "task_id": "t", "steps": [["a", "b", "c"]]}',
        "experiment.steps must be a list of [a, b] pairs",
    ),
    (
        "steps-non-string-entry",
        '{"action": "experiment", "task_id": "t", "steps": [["a", 2]]}',
        "experiment.steps must be a list of [a, b] pairs",
    ),
    (
        "steps-nested-too-deep",
        '{"action": "experiment", "task_id": "t", "steps": [[["a"], "b"]]}',
        "experiment.steps must be a list of [a, b] pairs",
    ),
    # ---------------------------------------------------------- note limit
    (
        "huge-note",
        json.dumps({"action": "note", "text": "x" * (NOTE_MAX_CHARS + 1)}),
        f"note.text exceeds {NOTE_MAX_CHARS} chars",
    ),
    (
        "gigantic-note",
        json.dumps({"action": "note", "text": "y" * 40_000}),
        f"note.text exceeds {NOTE_MAX_CHARS} chars",
    ),
]


@pytest.mark.parametrize(
    ("reply", "reason_prefix"),
    [case[1:] for case in INVALID_CASES],
    ids=[case[0] for case in INVALID_CASES],
)
def test_invalid(reply: str, reason_prefix: str) -> None:
    result = parse_action(reply)
    assert isinstance(result, ParseFailure)
    assert result.reason.startswith(reason_prefix)


# ------------------------------------------------------------ result hygiene


def test_args_never_include_action_key() -> None:
    result = parse_action('{"action": "travel", "to": "meadow"}')
    assert not isinstance(result, ParseFailure)
    _, args = result
    assert "action" not in args


def test_unknown_key_reported_deterministically() -> None:
    # Multiple unknown keys: the first in sorted order is named.
    result = parse_action('{"action": "rest", "zeta": 1, "alpha": 2}')
    assert result == ParseFailure(reason="unknown key 'alpha' for rest")


def test_missing_key_reported_deterministically() -> None:
    result = parse_action('{"action": "converse"}')
    assert result == ParseFailure(reason="missing key 'target' for converse")


def test_unknown_key_wins_over_missing_key() -> None:
    result = parse_action('{"action": "travel", "destination": "meadow"}')
    assert result == ParseFailure(reason="unknown key 'destination' for travel")


def test_note_at_exact_limit_is_valid_over_limit_is_not() -> None:
    ok = parse_action(json.dumps({"action": "note", "text": "n" * NOTE_MAX_CHARS}))
    assert ok == (ActionType.NOTE, {"text": "n" * NOTE_MAX_CHARS})
    bad = parse_action(json.dumps({"action": "note", "text": "n" * (NOTE_MAX_CHARS + 1)}))
    assert isinstance(bad, ParseFailure)


def test_parse_never_raises_on_junk() -> None:
    junk = ["{{{{", "}}}}", '{"a": "b"} {', "``` {", "\x00{}", '{"action": {}}', "[1, 2, 3]"]
    for reply in junk:
        result = parse_action(reply)
        assert isinstance(result, ParseFailure)


def test_parse_failure_model_is_frozen() -> None:
    failure = ParseFailure(reason="not JSON")
    with pytest.raises(Exception):  # pydantic frozen-instance error  # noqa: B017
        failure.reason = "other"  # type: ignore[misc]


# ------------------------------------------------------------ retry message


def test_retry_message_contains_reason_and_format() -> None:
    failure = ParseFailure(reason="unknown key 'foo' for converse")
    msg = retry_message(failure)
    assert "unknown key 'foo' for converse" in msg
    for action in ActionType:
        assert action.value in msg
    assert '"action"' in msg


def test_retry_message_is_terse() -> None:
    msg = retry_message(ParseFailure(reason="not JSON"))
    # One corrective line + the format restated in <= 3 lines.
    assert len(msg.splitlines()) <= 4
