"""Action parsing — sanitized model text in, validated action out.

The runner feeds this module the COMMITTED text (sanitized and
NFC-normalized); parsing is a pure function of that string. The result is
either ``(ActionType, args)`` with args containing only canonical-JSON
values (str/int/list — floats are rejected at the JSON layer, per the
no-floats rule), or a :class:`ParseFailure` whose ``reason`` is terse,
precise, and agent-readable — it is echoed back to the model on the single
billed retry (contracts: MAX_ACTION_PARSE_RETRIES).

Extraction is deliberately simple and total: strip one surrounding ```
fence if the whole reply is fenced, then balanced-brace-scan for the FIRST
complete top-level ``{...}`` (string- and escape-aware, so braces inside
JSON strings do not confuse it) and parse exactly that candidate. Leading
prose and trailing garbage around the object are tolerated; prose that
itself contains a ``{`` before the real object is not (the first balanced
region fails ``json.loads`` -> failure -> retry teaches the model). One
candidate, one verdict: no search over alternatives, so parsing never
depends on how much junk surrounds the object.

Failure-reason catalog (locked by tests):

- ``no JSON object found`` / ``unterminated JSON object``
- ``not JSON: <json error detail>``
- ``floats are not allowed; use plain integers``
- ``missing "action" key`` / ``action must be a string``
- ``unknown action '<x>'``
- ``unknown key '<k>' for <action>`` / ``missing key '<k>' for <action>``
- ``<action>.<field> must be a string``
- ``experiment.steps must be a list of [a, b] pairs``
- ``trade.stones must be an integer`` / ``trade.stones must be > 0``
- ``note.text exceeds 500 chars``
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ConfigDict

from lamarck.contracts import NOTE_MAX_CHARS, ActionType

__all__ = ["ParseFailure", "parse_action", "retry_message"]


class ParseFailure(BaseModel):
    """Why a model reply failed to parse/validate; ``reason`` is shown to
    the model verbatim inside :func:`retry_message`."""

    model_config = ConfigDict(frozen=True)

    reason: str


# Allowed argument keys per action ("action" itself is always allowed).
_ARG_KEYS: dict[ActionType, tuple[str, ...]] = {
    ActionType.EXPERIMENT: ("steps",),
    ActionType.CONVERSE: ("target", "text"),
    ActionType.TEACH: (),
    ActionType.STUDY: (),
    ActionType.TRADE: ("target", "stones"),
    ActionType.NOTE: ("text",),
    ActionType.TRAVEL: ("to",),
    ActionType.MEDITATE: (),
    ActionType.CHALLENGE: (),
    ActionType.ATTEMPT_BREAKTHROUGH: (),
    ActionType.REST: (),
}

# Fields that must be strings, per action.
_STR_FIELDS: dict[ActionType, tuple[str, ...]] = {
    ActionType.CONVERSE: ("target", "text"),
    ActionType.TRADE: ("target",),
    ActionType.NOTE: ("text",),
    ActionType.TRAVEL: ("to",),
}

_FENCE_RE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)\r?\n?```\s*$", re.DOTALL)

_ACTION_VALUES = ", ".join(a.value for a in ActionType)


class _FloatRejected(Exception):
    """Internal: a non-integer numeric literal (or NaN/Infinity) was seen."""


def _reject_float(literal: str) -> object:
    raise _FloatRejected(literal)


def _strip_fence(text: str) -> str:
    """Unwrap the body when the WHOLE (stripped) reply is one ``` fence."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1) if match else stripped


def _first_json_object(text: str) -> str | ParseFailure:
    """Return the first complete top-level ``{...}`` substring of *text*.

    A linear scan tracking string/escape state, so braces inside JSON
    strings never miscount depth. Fails when no ``{`` exists or the object
    never closes.
    """
    start = text.find("{")
    if start == -1:
        return ParseFailure(reason="no JSON object found")
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ParseFailure(reason="unterminated JSON object")


def _validate_args(action: ActionType, obj: dict[str, object]) -> dict[str, object] | ParseFailure:
    """Validate *obj*'s argument keys/types for *action*; return clean args."""
    allowed = _ARG_KEYS[action]
    present = [k for k in obj if k != "action"]
    unknown = sorted(k for k in present if k not in allowed)
    if unknown:
        return ParseFailure(reason=f"unknown key '{unknown[0]}' for {action.value}")
    missing = sorted(k for k in allowed if k not in obj)
    if missing:
        return ParseFailure(reason=f"missing key '{missing[0]}' for {action.value}")

    for field in _STR_FIELDS.get(action, ()):
        if not isinstance(obj[field], str):
            return ParseFailure(reason=f"{action.value}.{field} must be a string")

    if action is ActionType.NOTE:
        text = obj["text"]
        # isinstance re-check is mypy narrowing; the _STR_FIELDS pass above
        # already guaranteed str, so the branch condition is length alone.
        if isinstance(text, str) and len(text) > NOTE_MAX_CHARS:
            return ParseFailure(reason=f"note.text exceeds {NOTE_MAX_CHARS} chars")

    if action is ActionType.TRADE:
        stones = obj["stones"]
        if isinstance(stones, bool) or not isinstance(stones, int):
            return ParseFailure(reason="trade.stones must be an integer")
        if stones <= 0:
            return ParseFailure(reason="trade.stones must be > 0")

    if action is ActionType.EXPERIMENT:
        steps = obj["steps"]
        bad = ParseFailure(reason="experiment.steps must be a list of [a, b] pairs")
        if not isinstance(steps, list):
            return bad
        for step in steps:
            if not isinstance(step, list) or len(step) != 2:
                return bad
            if not all(isinstance(part, str) for part in step):
                return bad

    return {key: obj[key] for key in allowed}


def parse_action(text: str) -> tuple[ActionType, dict[str, object]] | ParseFailure:
    """Parse one model reply into ``(ActionType, canonicalizable args)``.

    See the module docstring for extraction rules and the failure-reason
    catalog. Never raises on model input: every malformed reply maps to a
    :class:`ParseFailure`.
    """
    candidate = _first_json_object(_strip_fence(text))
    if isinstance(candidate, ParseFailure):
        return candidate
    try:
        obj = json.loads(candidate, parse_float=_reject_float, parse_constant=_reject_float)
    except _FloatRejected:
        return ParseFailure(reason="floats are not allowed; use plain integers")
    except json.JSONDecodeError as err:
        return ParseFailure(reason=f"not JSON: {err.msg}")
    # candidate starts with "{", so a successful parse is always a dict
    if "action" not in obj:
        return ParseFailure(reason='missing "action" key')
    raw_action = obj["action"]
    if not isinstance(raw_action, str):
        return ParseFailure(reason="action must be a string")
    try:
        action = ActionType(raw_action)
    except ValueError:
        return ParseFailure(reason=f"unknown action '{raw_action}'")
    args = _validate_args(action, obj)
    if isinstance(args, ParseFailure):
        return args
    return (action, args)


def retry_message(failure: ParseFailure) -> str:
    """One terse corrective line plus the required format in <= 3 lines —
    appended to the conversation for the single billed retry."""
    return (
        f"Your reply was invalid: {failure.reason}.\n"
        'Reply with exactly one JSON object {"action": "<action>", ...} where <action> is one of: '
        f"{_ACTION_VALUES}.\n"
        'Args: experiment {"steps": [["a", "b"], ...]}; converse {"target", "text"}; '
        'travel {"to"}; trade {"target", "stones" > 0}; '
        f'note {{"text" <= {NOTE_MAX_CHARS} chars}}; all other actions take none.'
    )
