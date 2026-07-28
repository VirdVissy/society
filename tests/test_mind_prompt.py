"""Tests for lamarck.mind.prompt — the versioned, deterministic prompt boundary.

The golden strings below pin the EXACT rendered text of TEMPLATE_VERSION
p1.0. If any golden changes, the template wording changed: that commit MUST
bump TEMPLATE_VERSION (see the module docstring's bump rule) — a golden
diff without a version bump is a determinism bug, never a refactor.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import HeardUtterance, PerceptionView, PersonaCard, TaskStub
from lamarck.eventstore.canonical import canonical_bytes
from lamarck.mind.prompt import (
    TEMPLATE_VERSION,
    build_prompt,
    enforce_budget,
    prompt_sha,
    render_reflection_user,
    render_system,
    render_user,
)

# ------------------------------------------------------------------ fixture

PERSONA = PersonaCard(
    agent_id="a1",
    name="Yan Hua",
    temperament="patient and methodical",
    values=["mastery over wealth", "records outlive people"],
    quirks=["explains with cooking metaphors"],
    speech_style="measured, warm, precise",
)

VIEW = PerceptionView(
    day=3,
    round=2,
    tick=17,
    persona=PERSONA,
    location="furnace-hall",
    locations=["meadow", "furnace-hall", "cold-spring"],
    qi=1150000,
    stones=23,
    allowance_left=21500,
    co_present=["Bo Shan", "Mei Lin"],
    heard=[
        HeardUtterance(
            from_agent="a2", from_name="Bo Shan", text="The red ore cracks if you rush it."
        ),
        HeardUtterance(from_agent="a3", from_name="Mei Lin", text="Who trades for spring water?"),
    ],
    notes=["Cinnabar plus ash gave slag.", "Try spring water on the red ore."],
    reflection="Day 2 taught me patience: two failures, one clue about ash.",
    outcomes=[
        "The mixture seized into dull slag.",
        "A faint shimmer: the ash took the water.",
    ],
    tasks=[
        TaskStub(task_id="w1-01", tier=1, title='produce "cinnabar-ash"'),
        TaskStub(task_id="w2-03", tier=2, title='produce "quenched-iron"'),
    ],
    materials=[1, 2, 5, 12, 30],
    bounties=[10, 25, 60, 150, 400],
)

# ------------------------------------------------------------------ goldens

GOLDEN_SYSTEM = """\
You are Yan Hua, a cultivator of Wuxing Valley.
Temperament: patient and methodical
Values: mastery over wealth; records outlive people
Quirks: explains with cooking metaphors
Speech style: measured, warm, precise

THE WORLD
Wuxing Valley hides a true alchemy: materials combine by fixed rules that no
one alive remembers. Your qi is your life. It is finite, it never returns, and
every thought you form spends some of it; at zero qi you die. Verified
discoveries earn spirit stones — the valley's coin for materials and trade.

ACTION PROTOCOL
Respond with exactly one JSON object and nothing else. Its "action" key must be
one of: experiment, converse, teach, study, trade, note, travel, meditate,
challenge, attempt_breakthrough, rest.
Arguments by action (no other keys are accepted; numbers are plain integers):
  experiment {"task_id": "<task id>", "steps": [["<a>", "<b>"], ...]}
    Each step is a PAIR OF EXACT INGREDIENT NAMES to combine in the crucible —
    never instructions or descriptions. Usable names: the five bases (wood,
    fire, earth, metal, water) and the exact full name of any product an
    EARLIER step of this same attempt yielded (names may contain hyphens;
    copy them exactly). Any other name is not at hand and the attempt stops.
    CITE THE COMMISSION WHOSE PRODUCT YOU INTEND TO MAKE: you are paid only
    when the cited commission's own compound appears among your products.
    When an outcome says 'X fulfills wx-tN-i', claim it: attempt commission
    wx-tN-i with the exact steps that made X.
    Example (replace the task_id with the commission you are claiming):
    {"action": "experiment", "task_id": "wx-t1-0", "steps": [["wood", "fire"]]}
  converse {"target": "<name>", "text": "<what you say aloud>"}
  travel {"to": "<location>"}
  trade {"target": "<name>", "stones": <integer greater than 0>}
  note {"text": "<private note, at most 500 chars>"}
  teach, study, challenge, attempt_breakthrough, meditate, rest: no arguments.

Observe before you spend; the task board lists what the valley pays for.
Experiment in small steps and read what each combination leaves behind.
Speak with those beside you — knowledge shared compounds.
Write notes on what you learn; notes are the only memory that survives the day."""

GOLDEN_BODY = """\
STATUS
Qi: 1150000
Spirit stones: 23
Qi allowance left today: 21500
Location: furnace-hall (known places: meadow, furnace-hall, cold-spring)
Present with you: Bo Shan, Mei Lin

HEARD (oldest first)
Bo Shan: The red ore cracks if you rush it.
Mei Lin: Who trades for spring water?

YOUR NOTES (oldest first)
- Cinnabar plus ash gave slag.
- Try spring water on the red ore.

YOUR LAST REFLECTION
Day 2 taught me patience: two failures, one clue about ash.

RECENT EXPERIMENT OUTCOMES (oldest first)
- The mixture seized into dull slag.
- A faint shimmer: the ash took the water.

TASK BOARD
Base ingredients always at hand: wood, fire, earth, metal, water.
Combining unlocks products; a product's exact name becomes usable in later steps.
The board honors each commission once per cultivator; repeat verifications pay nothing.
- w1-01: produce "cinnabar-ash" (tier 1, materials 1 stones, bounty 10 stones)
- w2-03: produce "quenched-iron" (tier 2, materials 2 stones, bounty 25 stones)"""

GOLDEN_USER = (
    "Day 3, round 2, tick 17.\n\n"
    + GOLDEN_BODY
    + "\n\nChoose your action now. Reply with exactly one JSON object."
)

GOLDEN_REFLECTION = (
    "Dusk of day 3.\n\n"
    + GOLDEN_BODY
    + "\n\nDusk has fallen. In at most 3 sentences, write your private diary of this day: "
    "what you tried, what you learned, what you intend tomorrow. "
    "Name any commission you now know how to fulfill but have not yet claimed. "
    "Reply with the diary text only — no JSON."
)

# sha256 of the utf-8 canonical envelope over (GOLDEN_SYSTEM, GOLDEN_USER).
GOLDEN_PROMPT_SHA = "b65c62c093e2fd2aa7815954c3d3963fccadc40ca39cfa27574415c5716a0275"


# ------------------------------------------------------------------- goldens


def test_template_version_pinned() -> None:
    assert TEMPLATE_VERSION == "p1.4"


def test_golden_system() -> None:
    assert render_system(PERSONA) == GOLDEN_SYSTEM


def test_golden_user() -> None:
    assert render_user(VIEW) == GOLDEN_USER


def test_golden_reflection_user() -> None:
    assert render_reflection_user(VIEW) == GOLDEN_REFLECTION


def test_render_is_deterministic() -> None:
    assert render_user(VIEW) == render_user(VIEW)
    assert render_system(PERSONA) == render_system(PERSONA)
    assert build_prompt("s", "u") == build_prompt("s", "u")


# ------------------------------------------------------------------ envelope


def test_envelope_is_canonical_json() -> None:
    prompt = build_prompt(GOLDEN_SYSTEM, GOLDEN_USER)
    parsed = json.loads(prompt)
    assert set(parsed) == {"system", "template", "user"}
    assert parsed["system"] == GOLDEN_SYSTEM
    assert parsed["user"] == GOLDEN_USER
    # Canonical re-encoding of the parsed envelope is byte-identical: the
    # envelope IS canonical JSON, produced by the one true serializer.
    assert canonical_bytes(parsed) == prompt.encode("utf-8")


def test_envelope_carries_template_version() -> None:
    parsed = json.loads(build_prompt("s", "u"))
    assert parsed["template"] == TEMPLATE_VERSION


def test_prompt_sha_definition_and_pin() -> None:
    prompt = build_prompt(GOLDEN_SYSTEM, GOLDEN_USER)
    # Definition: sha256_hex(utf8(prompt)) — recomputed with hashlib alone.
    assert prompt_sha(prompt) == hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    # Pin: the run-fingerprint seam for TEMPLATE_VERSION p1.0.
    assert prompt_sha(prompt) == GOLDEN_PROMPT_SHA


def test_prompt_sha_stability_across_calls() -> None:
    prompt = build_prompt(render_system(PERSONA), render_user(VIEW))
    assert prompt_sha(prompt) == prompt_sha(prompt) == GOLDEN_PROMPT_SHA


# -------------------------------------------------------------------- budget


def test_budget_noop_when_within() -> None:
    assert enforce_budget(VIEW, len(render_user(VIEW))) == VIEW
    assert enforce_budget(VIEW, 10_000_000) == VIEW


def test_budget_drops_oldest_heard_first() -> None:
    budget = len(render_user(VIEW)) - 1
    shrunk = enforce_budget(VIEW, budget)
    assert shrunk.heard == VIEW.heard[1:]  # oldest heard dropped, nothing else
    assert shrunk.notes == VIEW.notes
    assert shrunk.outcomes == VIEW.outcomes
    assert len(render_user(shrunk)) <= budget


def test_budget_drop_order_heard_then_notes_then_outcomes() -> None:
    # Target: all heard gone, all notes gone, oldest outcome gone. Set the
    # budget to exactly the target's rendered size; the loop must land there
    # by dropping, in order: heard[0], heard[1], notes[0], notes[1],
    # outcomes[0] — one at a time, re-rendering each time.
    target = VIEW.model_copy(update={"heard": [], "notes": [], "outcomes": VIEW.outcomes[1:]})
    budget = len(render_user(target))
    shrunk = enforce_budget(VIEW, budget)
    assert shrunk.heard == []
    assert shrunk.notes == []
    assert shrunk.outcomes == VIEW.outcomes[1:]
    assert shrunk == target
    # Every prefix of the drop sequence still exceeds the budget: dropping
    # anything less would not have satisfied it (proves order was forced).
    over_budget_prefixes = [
        VIEW,
        VIEW.model_copy(update={"heard": VIEW.heard[1:]}),
        VIEW.model_copy(update={"heard": []}),
        VIEW.model_copy(update={"heard": [], "notes": VIEW.notes[1:]}),
        VIEW.model_copy(update={"heard": [], "notes": []}),
    ]
    for prefix in over_budget_prefixes:
        assert len(render_user(prefix)) > budget


def test_budget_deterministic() -> None:
    budget = len(render_user(VIEW)) - 40
    assert enforce_budget(VIEW, budget) == enforce_budget(VIEW, budget)


def test_budget_floor_asserts_when_nothing_left_to_drop() -> None:
    floor = VIEW.model_copy(update={"heard": [], "notes": [], "outcomes": []})
    with pytest.raises(LamarckAssertionError, match="floor view exceeds prompt budget"):
        enforce_budget(floor, 10)


# ------------------------------------------------------- section edge cases


def test_empty_sections_render_placeholders() -> None:
    empty = VIEW.model_copy(
        update={
            "co_present": [],
            "heard": [],
            "notes": [],
            "reflection": "",
            "outcomes": [],
            "tasks": [],
        }
    )
    text = render_user(empty)
    assert "Present with you: no one" in text
    assert "HEARD (oldest first)\n(nothing)" in text
    assert "YOUR NOTES (oldest first)\n(none)" in text
    assert "YOUR LAST REFLECTION\n(none)" in text
    assert "RECENT EXPERIMENT OUTCOMES (oldest first)\n(none)" in text
    assert (
        "TASK BOARD\n"
        "Base ingredients always at hand: wood, fire, earth, metal, water.\n"
        "Combining unlocks products; a product's exact name becomes usable in later steps.\n"
        "The board honors each commission once per cultivator; repeat verifications pay nothing.\n"
        "(no tasks posted)"
    ) in text


def test_task_tier_outside_tables_asserts() -> None:
    bad = VIEW.model_copy(update={"materials": [1], "bounties": [10]})
    with pytest.raises(LamarckAssertionError, match="task tier outside"):
        render_user(bad)


def test_lists_render_in_given_order() -> None:
    flipped = VIEW.model_copy(update={"heard": list(reversed(VIEW.heard))})
    text = render_user(flipped)
    assert text.index("Mei Lin: Who trades") < text.index("Bo Shan: The red ore")
