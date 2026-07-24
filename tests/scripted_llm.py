"""Deterministic fake-LLM policy for ScriptedBackend — the live-golden brain.

``make(seed=0)`` returns a fresh ``(prompt, params) -> str`` policy suitable
for ``ScriptedBackend(callable)`` (and for the CLI seam
``lamarck live --scripted-module tests.scripted_llm:make``). It is PURE and
SEEDED: all state lives in the closure, keyed by the persona name parsed
from the prompt, so the composed run is a pure function of (config bytes,
this module, seed) and two fresh runs replay byte-identically.

The policy parses the runner's own template output minimally and robustly:
the envelope is canonical JSON ``{"system", "template", "user"}``; the agent
name comes from the system line ``You are <name>, a cultivator``; the user
text yields the day header (``Day d, round r, tick t.`` / ``Dusk of day
d.``), STATUS numbers, the location and known places, co-present names,
HEARD speakers, RECENT EXPERIMENT OUTCOMES messages, and the TASK BOARD
(task_id, target compound, tier, materials, bounty). A retry call is
recognized by the appended ``Your reply was invalid`` corrective line.

Experiment strategy (bases first, then build on own discoveries):

- probe the 10 unordered base pairs one per experiment slot, starting at a
  per-agent offset (sha256(name) + seed, mod 10);
- attribute the newest outcome message to the last submitted steps (the
  policy only submits experiments it can afford — allowance and stones are
  self-checked from STATUS/board — so every submission commits and the
  newest outcome is always its own);
- when a learned product matches a task-board target, CLAIM it by
  resubmitting the recorded derivation (a guaranteed verified discovery);
- once probes run out, build upward: known-product + base, attached to an
  unclaimed task of the pair's plausible tier.

Coverage schedule per agent (k = 0-based decision count; retries do not
increment k; ``off`` = the per-agent offset):

- k == 1  converse (seeds HEARD lines for co-located agents)
- k == 3  MALFORMED prose reply -> retry -> meditate   (retry recovery)
- k == 5  travel to an unknown place -> retry -> real travel (semantic fail)
- k == 7  float-in-args trade (stones: 1.5) -> retry -> note (parse fail)
- k == 9 + (off % 5)  DOUBLY-MALFORMED pair -> forfeit (exactly once/agent)
- k == 15 trade 1 stone to the first co-present neighbour (else note)
- k % 4 == 2 (otherwise) social rotation: converse / note / study
- everything else: the experiment strategy (meditate when unaffordable)

Every reply stays far below 4 * max_tokens characters so synthesized usage
(``ceil(len/4)``) respects the runner's worst-case allowance pre-check.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import NamedTuple

from lamarck.contracts import GenParams

__all__ = ["make"]

_BASES = ("wood", "fire", "earth", "metal", "water")
_BASE_PAIRS: tuple[tuple[str, str], ...] = tuple(
    (_BASES[i], _BASES[j]) for i in range(len(_BASES)) for j in range(i + 1, len(_BASES))
)  # 10 unordered pairs in a fixed, documented order

_NAME_RE = re.compile(r"^You are (?P<name>.+?), a cultivator", re.MULTILINE)
_DAY_RE = re.compile(r"^Day (?P<day>\d+), round (?P<round>\d+), tick (?P<tick>\d+)\.")
_DUSK_RE = re.compile(r"^Dusk of day (?P<day>\d+)\.")
_STONES_RE = re.compile(r"^Spirit stones: (?P<stones>\d+)$", re.MULTILINE)
_ALLOWANCE_RE = re.compile(r"^Qi allowance left today: (?P<left>\d+)$", re.MULTILINE)
_LOCATION_RE = re.compile(
    r"^Location: (?P<loc>.+?) \(known places: (?P<places>.+?)\)$", re.MULTILINE
)
_PRESENT_RE = re.compile(r"^Present with you: (?P<names>.+)$", re.MULTILINE)
_TASK_RE = re.compile(
    r'^- (?P<tid>[\w-]+): produce "(?P<target>[^"]+)" \(tier (?P<tier>\d+), '
    r"materials (?P<mat>\d+) stones, bounty (?P<bounty>\d+) stones\)$",
    re.MULTILINE,
)
_YIELD_RE = re.compile(r"the crucible yields (?P<product>[a-z-]+)")

_RETRY_MARKER = "Your reply was invalid"
_MIN_THINK_ALLOWANCE = 2000  # self-check floor before submitting a surcharged action


class _Task(NamedTuple):
    task_id: str
    target: str
    tier: int
    materials: int
    bounty: int


class _View(NamedTuple):
    """Everything the policy reads out of one user text."""

    day: int
    is_dusk: bool
    is_retry: bool
    stones: int
    allowance: int
    location: str
    places: list[str]
    co_present: list[str]
    heard_names: list[str]
    outcomes: list[str]
    tasks: list[_Task]


def _parse_user(user: str) -> _View:
    dusk = _DUSK_RE.match(user)
    day_match = _DAY_RE.match(user)
    day = int(dusk.group("day")) if dusk else int(day_match.group("day")) if day_match else 0
    stones_m = _STONES_RE.search(user)
    allowance_m = _ALLOWANCE_RE.search(user)
    loc_m = _LOCATION_RE.search(user)
    present_m = _PRESENT_RE.search(user)
    present_raw = present_m.group("names") if present_m else "no one"
    co_present = [] if present_raw == "no one" else present_raw.split(", ")
    heard_names: list[str] = []
    outcomes: list[str] = []
    for block in user.split("\n\n"):
        lines = block.split("\n")
        if lines[0] == "HEARD (oldest first)":
            for line in lines[1:]:
                speaker, sep, _ = line.partition(": ")
                if sep and line != "(nothing)":
                    heard_names.append(speaker)
        elif lines[0] == "RECENT EXPERIMENT OUTCOMES (oldest first)":
            outcomes = [line[2:] for line in lines[1:] if line.startswith("- ")]
    tasks = [
        _Task(m["tid"], m["target"], int(m["tier"]), int(m["mat"]), int(m["bounty"]))
        for m in _TASK_RE.finditer(user)
    ]
    return _View(
        day=day,
        is_dusk=dusk is not None,
        is_retry=_RETRY_MARKER in user,
        stones=int(stones_m.group("stones")) if stones_m else 0,
        allowance=int(allowance_m.group("left")) if allowance_m else 0,
        location=loc_m.group("loc") if loc_m else "",
        places=loc_m.group("places").split(", ") if loc_m else [],
        co_present=co_present,
        heard_names=heard_names,
        outcomes=outcomes,
        tasks=tasks,
    )


class _Mind:
    """Per-agent mutable policy state (keyed by persona name)."""

    def __init__(self, offset: int) -> None:
        self.offset = offset
        self.calls = 0  # decisions made (retries excluded)
        self.probe_idx = 0  # next base-pair probe
        self.derivations: dict[str, list[list[str]]] = {}  # product -> steps that made it
        self.pending: list[list[str]] | None = None  # steps of the last experiment
        self.claimed: set[str] = set()  # task_ids already claimed by this agent
        self.social_idx = 0
        self.build_idx = 0
        self.last_special: str | None = None


def _action(action: str, **args: object) -> str:
    return json.dumps({"action": action, **args})


def make(seed: int = 0) -> Callable[[str, GenParams], str]:
    """Build a fresh, deterministic scripted-LLM policy (see module docstring)."""
    minds: dict[str, _Mind] = {}

    def mind_for(name: str) -> _Mind:
        existing = minds.get(name)
        if existing is not None:
            return existing
        digest = hashlib.sha256(name.encode("utf-8")).digest()
        created = _Mind(offset=(int.from_bytes(digest[:2], "big") + seed) % len(_BASE_PAIRS))
        minds[name] = created
        return created

    def learn(mind: _Mind, view: _View) -> None:
        """Attribute the newest outcome to the last submitted steps."""
        if mind.pending is None or not view.outcomes:
            return
        match = _YIELD_RE.search(view.outcomes[-1])
        if match:
            product = match.group("product")
            if product != "slag" and product not in mind.derivations:
                mind.derivations[product] = mind.pending
        mind.pending = None

    def experiment(mind: _Mind, view: _View) -> str:
        """Claim > probe > build; meditate when nothing is affordable."""
        if view.allowance < _MIN_THINK_ALLOWANCE:
            return _action("meditate")
        by_target = {t.target: t for t in view.tasks}
        for product in sorted(mind.derivations):  # deterministic claim order
            task = by_target.get(product)
            if task is None or task.task_id in mind.claimed or view.stones < task.materials:
                continue
            mind.claimed.add(task.task_id)
            mind.pending = mind.derivations[product]
            return _action("experiment", task_id=task.task_id, steps=mind.derivations[product])
        tier1 = [t for t in view.tasks if t.tier == 1]
        if mind.probe_idx < len(_BASE_PAIRS) and tier1 and view.stones >= tier1[0].materials:
            pair = _BASE_PAIRS[(mind.offset + mind.probe_idx) % len(_BASE_PAIRS)]
            mind.probe_idx += 1
            anchor = next((t for t in tier1 if t.task_id not in mind.claimed), tier1[0])
            steps = [[pair[0], pair[1]]]
            mind.pending = steps
            return _action("experiment", task_id=anchor.task_id, steps=steps)
        if mind.derivations:
            products = sorted(mind.derivations)
            product = products[mind.build_idx % len(products)]
            base = _BASES[mind.build_idx % len(_BASES)]
            mind.build_idx += 1
            steps = mind.derivations[product] + [[product, base]]
            anchors = [t for t in view.tasks if t.tier == 2] or view.tasks
            if not anchors or view.stones < anchors[0].materials or len(steps) > 12:
                return _action("meditate")
            anchor = next((t for t in anchors if t.task_id not in mind.claimed), anchors[0])
            mind.pending = steps
            return _action("experiment", task_id=anchor.task_id, steps=steps)
        return _action("meditate")

    def social(mind: _Mind, view: _View, name: str) -> str:
        kind = ("converse", "note", "study")[mind.social_idx % 3]
        mind.social_idx += 1
        if kind == "converse":
            target = view.co_present[0] if view.co_present else name
            products = sorted(mind.derivations)
            if products:
                text = f"I have drawn {products[-1]} from the crucible."
            elif view.heard_names:
                text = f"I hear you, {view.heard_names[-1]}. The rules hide well."
            else:
                text = "The valley keeps its silence today."
            return _action("converse", target=target, text=text)
        if kind == "note":
            products = sorted(mind.derivations)
            if products:
                latest = products[-1]
                pair = mind.derivations[latest][-1]
                text = f"learned: {pair[0]}+{pair[1]} -> {latest}"
            else:
                text = f"day {view.day}: nothing learned yet"
            return _action("note", text=text)
        return _action("study")

    def retry_reply(mind: _Mind, view: _View) -> str:
        special = mind.last_special
        mind.last_special = None
        if special == "double":
            return "Still I refuse the form. The mountain does not answer to JSON."
        if special == "badloc":
            if view.location in view.places and len(view.places) > 1:
                here = view.places.index(view.location)
                return _action("travel", to=view.places[(here + 1) % len(view.places)])
            return _action("meditate")
        if special == "float":
            return _action("note", text="the ledger counts whole stones only")
        return _action("meditate")

    def policy(prompt: str, params: GenParams) -> str:
        envelope = json.loads(prompt)
        system = envelope["system"]
        user = envelope["user"]
        name_match = _NAME_RE.search(system)
        name = name_match.group("name") if name_match else "unknown"
        mind = mind_for(name)
        view = _parse_user(user)

        if view.is_dusk:
            learn(mind, view)
            return (
                f"Day {view.day} closes over {view.location}. I hold {len(mind.derivations)} "
                f"learnings and {view.stones} stones; tomorrow the crucible again."
            )
        if view.is_retry:
            return retry_reply(mind, view)

        learn(mind, view)
        k = mind.calls
        mind.calls += 1

        if k == 3:
            mind.last_special = "malformed"
            return "I sit with the question a while longer."
        if k == 5:
            mind.last_special = "badloc"
            return _action("travel", to="the-shrouded-peak")
        if k == 7:
            mind.last_special = "float"
            return f'{{"action": "trade", "target": "{name}", "stones": 1.5}}'
        if k == 9 + (mind.offset % 5):
            mind.last_special = "double"
            return "Words fail; the crucible alone speaks."
        if k == 15:
            if view.co_present and view.stones >= 2:
                return _action("trade", target=view.co_present[0], stones=1)
            return _action("note", text="no neighbour to trade with today")
        if k == 1:
            target = view.co_present[0] if view.co_present else name
            return _action("converse", target=target, text="Shall we split the board between us?")
        if k % 4 == 2:
            return social(mind, view, name)
        return experiment(mind, view)

    return policy
