"""Prompt rendering — the determinism boundary made visible.

A live agent's prompt is a PURE function of ``(PersonaCard, PerceptionView,
TEMPLATE_VERSION)`` and nothing else: no clocks, no randomness, no dict
iteration over unordered state (every ``PerceptionView`` collection is an
ordered list rendered in the given order). The runner hashes the rendered
envelope (``prompt_sha``) into the LLM_CALL payload and stores the full text
unhashed in ``lamarck.eventstore.texts`` — deep replay re-renders and must
reproduce the sha byte-for-byte.

TEMPLATE VERSIONING: ``TEMPLATE_VERSION`` names the exact wording of every
string this module renders. ANY change to that wording — a comma, a space, a
reordered line, a section heading — MUST bump ``TEMPLATE_VERSION`` (p1.0 ->
p1.1, ...) in the same commit; the golden tests pin the rendered text and
will fail on unversioned drift. Two runs with the same template version and
the same views produce identical prompt shas, forever.

The envelope handed to backends is the canonical-JSON string
``{"system": ..., "template": TEMPLATE_VERSION, "user": ...}`` (encoded by
``lamarck.eventstore.canonical`` — the one true serializer; this module
never hand-rolls JSON).
"""

from __future__ import annotations

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import NOTE_MAX_CHARS, PerceptionView, PersonaCard
from lamarck.eventstore.canonical import canonical_bytes, sha256_hex

__all__ = [
    "TEMPLATE_VERSION",
    "build_prompt",
    "enforce_budget",
    "prompt_sha",
    "render_reflection_user",
    "render_system",
    "render_user",
]

TEMPLATE_VERSION = "p2.3"  # p2.3: lab journal (p2.2 satchel-ladder; p2.1 auto-claim; p2.0 satchel)

_ACTION_CLOSING = "Choose your action now. Reply with exactly one JSON object."
_REFLECTION_CLOSING = (
    "Dusk has fallen. In at most 3 sentences, write your private diary of this day: "
    "what you tried, what you learned, what you intend tomorrow. "
    "Name any commission you now know how to fulfill but have not yet claimed. "
    "Reply with the diary text only — no JSON."
)


def render_system(persona: PersonaCard) -> str:
    """Render the per-agent system text: identity, world briefing, protocol.

    Stable per agent: depends on the PersonaCard alone (personas are
    immutable birth identity), so an agent's system text never changes
    within a run — or across runs — under one TEMPLATE_VERSION.
    """
    values = "; ".join(persona.values)
    quirks = "; ".join(persona.quirks)
    return (
        f"You are {persona.name}, a cultivator of Wuxing Valley.\n"
        f"Temperament: {persona.temperament}\n"
        f"Values: {values}\n"
        f"Quirks: {quirks}\n"
        f"Speech style: {persona.speech_style}\n"
        "\n"
        "THE WORLD\n"
        "Wuxing Valley hides a true alchemy: materials combine by fixed rules that no\n"
        "one alive remembers. Your qi is your life. It is finite, it never returns, and\n"
        "every thought you form spends some of it; at zero qi you die. Verified\n"
        "discoveries earn spirit stones — the valley's coin for materials and trade.\n"
        "\n"
        "ACTION PROTOCOL\n"
        'Respond with exactly one JSON object and nothing else. Its "action" key must be\n'
        "one of: experiment, converse, teach, study, trade, note, travel, meditate,\n"
        "challenge, attempt_breakthrough, rest.\n"
        "Arguments by action (no other keys are accepted; numbers are plain integers):\n"
        '  experiment {"steps": [["<a>", "<b>"], ...]}\n'
        "    Each step is a PAIR OF EXACT INGREDIENT NAMES to combine in the crucible —\n"
        "    never instructions or descriptions. Usable names: the five bases (wood,\n"
        "    fire, earth, metal, water), everything in YOUR SATCHEL, and any product\n"
        "    an earlier step of this same attempt yielded (names may contain hyphens;\n"
        "    copy them exactly). Any other name is not at hand and the attempt stops.\n"
        "    The board pays you AUTOMATICALLY the first time you produce a\n"
        "    commissioned compound — just experiment; no paperwork.\n"
        '    Example: {"action": "experiment", "steps": [["wood", "fire"]]}\n'
        '  converse {"target": "<name>", "text": "<what you say aloud>"}\n'
        '  travel {"to": "<location>"}\n'
        '  trade {"target": "<name>", "stones": <integer greater than 0>}\n'
        f'  note {{"text": "<private note, at most {NOTE_MAX_CHARS} chars>"}}\n'
        "  teach, study, challenge, attempt_breakthrough, meditate, rest: no arguments.\n"
        "\n"
        "Observe before you spend; the task board lists what the valley pays for.\n"
        "Experiment in small steps and read what each combination leaves behind.\n"
        "The five base pairs run out fast: higher commissions come from combining\n"
        "your satchel compounds with bases and with each other.\n"
        "Your lab journal below records every pair you have ever tried and what it\n"
        "gave: repeating a journal entry can never teach you anything new — spend\n"
        "experiments only on pairs the journal does not contain.\n"
        "Speak with those beside you — knowledge shared compounds.\n"
        "Write notes on what you learn; notes are the only memory that survives the day."
    )


def _status_block(view: PerceptionView) -> str:
    present = ", ".join(view.co_present) if view.co_present else "no one"
    places = ", ".join(view.locations)
    return (
        "STATUS\n"
        f"Qi: {view.qi}\n"
        f"Spirit stones: {view.stones}\n"
        f"Qi allowance left today: {view.allowance_left}\n"
        f"Location: {view.location} (known places: {places})\n"
        f"Present with you: {present}"
    )


def _heard_block(view: PerceptionView) -> str:
    lines = ["HEARD (oldest first)"]
    if view.heard:
        lines.extend(f"{u.from_name}: {u.text}" for u in view.heard)
    else:
        lines.append("(nothing)")
    return "\n".join(lines)


def _notes_block(view: PerceptionView) -> str:
    lines = ["YOUR NOTES (oldest first)"]
    if view.notes:
        lines.extend(f"- {note}" for note in view.notes)
    else:
        lines.append("(none)")
    return "\n".join(lines)


def _reflection_block(view: PerceptionView) -> str:
    body = view.reflection if view.reflection else "(none)"
    return f"YOUR LAST REFLECTION\n{body}"


def _outcomes_block(view: PerceptionView) -> str:
    lines = ["RECENT EXPERIMENT OUTCOMES (oldest first)"]
    if view.outcomes:
        lines.extend(f"- {outcome}" for outcome in view.outcomes)
    else:
        lines.append("(none)")
    return "\n".join(lines)


def _journal_block(view: PerceptionView) -> str:
    """Successes one line each; slag pairs grouped on one line — compact,
    cumulative, and rendered in first-tried order (slag order included)."""
    lines = ["YOUR LAB JOURNAL (every pair you have tried; do not repeat these)"]
    if not view.journal:
        lines.append("(nothing tried yet)")
        return "\n".join(lines)
    slag_pairs = [f"{a}+{b}" for a, b, product in view.journal if product == "slag"]
    lines.extend(f"{a} + {b} -> {product}" for a, b, product in view.journal if product != "slag")
    if slag_pairs:
        lines.append("Slag (dead ends, never retry): " + ", ".join(slag_pairs))
    return "\n".join(lines)


def _satchel_block(view: PerceptionView) -> str:
    lines = ["YOUR SATCHEL (everything you have made; usable as ingredients)"]
    if view.satchel:
        lines.extend(f"- {item}" for item in view.satchel)
    else:
        lines.append("(empty)")
    return "\n".join(lines)


def _tasks_block(view: PerceptionView) -> str:
    lines = [
        "TASK BOARD",
        "Base ingredients always at hand: wood, fire, earth, metal, water.",
        "Products you make join your satchel and stay usable as ingredients forever.",
        "Each commission pays automatically on your first production of its compound.",
    ]
    if view.tasks:
        for task in view.tasks:
            LMK_ASSERT(
                1 <= task.tier <= len(view.materials) and task.tier <= len(view.bounties),
                "task tier outside materials/bounties tables",
                task_id=task.task_id,
                tier=task.tier,
            )
            lines.append(
                f"- {task.task_id}: {task.title} (tier {task.tier}, "
                f"materials {view.materials[task.tier - 1]} stones, "
                f"bounty {view.bounties[task.tier - 1]} stones)"
            )
    else:
        lines.append("(no tasks posted)")
    return "\n".join(lines)


def _assemble(header: str, view: PerceptionView, closing: str) -> str:
    blocks = [
        header,
        _status_block(view),
        _heard_block(view),
        _notes_block(view),
        _reflection_block(view),
        _outcomes_block(view),
        _satchel_block(view),
        _journal_block(view),
        _tasks_block(view),
        closing,
    ]
    return "\n\n".join(blocks)


def render_user(view: PerceptionView) -> str:
    """Render the action-tick user text: fixed section order, given-order
    lists, no timestamps, no randomness. Deterministic per view."""
    header = f"Day {view.day}, round {view.round}, tick {view.tick}."
    return _assemble(header, view, _ACTION_CLOSING)


def render_reflection_user(view: PerceptionView) -> str:
    """Dusk variant: same sections, asks for a <=3-sentence diary instead of
    a JSON action (the REFLECTION event stores the sanitized text)."""
    return _assemble(f"Dusk of day {view.day}.", view, _REFLECTION_CLOSING)


def build_prompt(system: str, user: str) -> str:
    """The canonical-JSON envelope string handed to every backend.

    Keys sort canonically as system < template < user; the template version
    rides inside the hashed prompt, so a template bump changes every
    prompt_sha by construction.
    """
    envelope = {"system": system, "template": TEMPLATE_VERSION, "user": user}
    return canonical_bytes(envelope).decode("utf-8")


def prompt_sha(prompt: str) -> str:
    """sha256 hex of the UTF-8 bytes of *prompt* — the hashed LLM_CALL field."""
    return sha256_hex(prompt.encode("utf-8"))


def enforce_budget(view: PerceptionView, budget_chars: int) -> PerceptionView:
    """Shrink *view* until ``render_user(view)`` fits in *budget_chars*.

    Drop order (locked, tested): oldest heard first, then oldest notes, then
    oldest outcomes, then — last resort, the journal is the anti-retread
    memory — oldest journal entries; one item at a time, re-rendering after
    each drop (all four lists are ordered oldest-relevant-first at index 0
    for dropping: heard is most-recent-last; notes, outcomes, and journal
    are oldest-first). Deterministic. When nothing is left to drop and the
    floor view still exceeds the budget, that is a config/render bug:
    LMK_ASSERT fires.
    """
    current = view
    while len(render_user(current)) > budget_chars:
        if current.heard:
            current = current.model_copy(update={"heard": list(current.heard[1:])})
        elif current.notes:
            current = current.model_copy(update={"notes": list(current.notes[1:])})
        elif current.outcomes:
            current = current.model_copy(update={"outcomes": list(current.outcomes[1:])})
        elif current.journal:
            current = current.model_copy(update={"journal": list(current.journal[1:])})
        else:
            LMK_ASSERT(
                False,
                "floor view exceeds prompt budget",
                budget_chars=budget_chars,
                rendered_chars=len(render_user(current)),
                agent=view.persona.agent_id,
            )
    return current
