"""Recipe exposure — retro-provenance over a finished run's event log.

The $0 prototype of Phase 2's teaching-attribution rule (plan D1). Everything
here is a PURE fold over committed ``EventRecord``s: ``write_exposure(run_dir)``
reads ``events.sqlite3`` (plus ``config.toml`` to rebuild the universe's name
list, and the unhashed ``llm_texts`` side table when present, read-only) and
writes ``exposure.json``. The output holds ints, strings, bools, lists, dicts
and null only — never floats (rates are permille ints, floor-divided);
ordering is deterministic everywhere (agents in spawn order, seqs ascending,
keys sorted at serialization), so two writes over the same log are
byte-identical. The module never mutates a run directory except to write its
own ``exposure.json`` (``out_path`` redirects even that).

RULES (the deliverable; the numbered wording is the brief's, followed by the
exact reading this module implements)

R1 Recipe truth. Fold every TASK_ATTEMPT: for each executed step i with
   step_products[i] != "slag", (sorted pair) -> product is a recipe; the
   global recipe map is the union over all agents; per-agent journal = the
   pairs that agent has executed with their product (first-tried order),
   per-agent satchel = non-slag products the agent has produced. Maintained
   incrementally in seq order.
   - executed steps = ``zip(steps, step_products)`` (the executed prefix; a
     halted attempt executes fewer steps than it lists); a malformed step
     (not a two-string list) stops the attempt's fold. ``KnowledgeFold``
     below is this module's own fold; the test suite asserts its journal and
     satchel equal ``WorldStateFold``'s on a scripted run.

R2 Sentence split: split utterance/note text on any of . ! ? ; : and newline;
   a sentence "names pair (a,b)" iff it contains both names, where base names
   match word-bounded (regex \\b) case-insensitively and compound names match
   as whole hyphenated tokens (word-bounded too); "names product t" iff it
   also contains t.
   - the terminator stays attached to its sentence (so R6's '?' exclusion is
     meaningful); sentences are stripped, empties dropped;
   - compound tokens: ``(?<![\\w-])name(?![\\w-])``, also case-insensitive;
     a base mention lying inside a compound mention's span is dropped (the
     wuxing generator forbids base-in-compound names; hand-made lists may
     not);
   - WHICH pairs a sentence names is R3's pair rule (``pair_rule``); the
     "names product t" test is sentence-level under either rule.

R3 Exposure event: a non-degraded CONVERSE (ACTION payload type converse, no
   degraded) whose text has a sentence naming pair (a,b) such that the
   SPEAKER's journal at that seq holds (a,b)->t with t != slag. One exposure
   record per (utterance, recipe, listener) for every listener the world fold
   delivered it to (co-located, alive, not the speaker, at emission). Record:
   utterance_seq, day, speaker, listener, a, b, target t, tier (from the
   LEDGER_ADJUST/claims tier of t if known else 0), product_named (bool),
   listener_naive (pair not in listener's journal before this seq),
   listener_can_make (both a and b in BASES ∪ listener satchel at that seq).
   - PAIR RULE (``pair_rule``, two readings of "a sentence names pair (a,b)";
     both are always measured, the selected one drives the records):
     * "connected" (the DEFAULT and the frozen Phase-2 attribution rule): a
       sentence names pair (a,b) iff a and b are two consecutive ingredient
       mentions (bases/compounds, different names) whose gap is only pair
       connectors — ``+ - / &``, and, with, plus, paired with, together
       with, against, any whitespace — taken left to right without overlap
       and deduped (R6's ``_connected_pairs``). So "fire-metal",
       "fire + metal", "fire and metal" and "fire with metal" all name
       (fire, metal); "fire, then earth" names nothing.
     * "sentence": a sentence names pair (a,b) iff it mentions both names
       anywhere — ALL unordered pairs of the distinct names it mentions (the
       original minimal rule, kept so the two can be compared).
     Under both, "names product t" holds iff t is mentioned anywhere in the
     same sentence. Rationale: precision of the frozen rule. Enumerations
     such as "fire-metal gave whispering-bone, earth-metal gave
     carmine-pearl, metal-water gave silt-ash" mention many ingredients in
     one sentence; under "sentence" every pair among them that the speaker
     happens to hold counts as taught (most of the accepted run's records
     arise this way), under "connected" only the pairs the speaker actually
     joined do. Everything downstream (R4 uptake and control, R5, per-agent,
     examples, the ``distinct_*`` counts) follows the selected rule; R6 is
     unaffected (it has always used the connected reading).
   - recipes are deduped per utterance across its sentences; product_named
     is true when ANY sentence of the utterance names both the pair and t;
   - tier: ``RecipeNames.tiers`` (from the config's universe) first, else the
     log — 'The board pays N stones for X (task_id).' joined with the claim's
     tier — else 0.

R4 Uptake: for each exposure with listener_naive, uptake = the listener's
   first executed step on pair (a,b) at a later seq with day - utterance day
   <= window_days; record uptake_seq, uptake_delay_days, produced_target
   (bool). Also compute the CONTROL rate: for each agent-day, the set of
   untried pairs the agent could make (all unordered pairs over BASES ∪
   satchel, excluding pairs in its journal), minus pairs mentioned to it in
   any exposure within [day-window, day+window]; control_uptake = fraction of
   those (agent-day, pair) that the agent first-tries within window_days.
   Report both as permille with numerators/denominators.
   - agent-day = each DAY_STARTED event × each agent alive at that event;
     the snapshot is the state at that seq (day start); pairs are distinct
     (a < b); "mentioned to it" = any exposure record with listener = agent
     and the same pair; permille = ``uptake * 1000 // denominator`` (0 when
     the denominator is 0).

R5 Acquisition classification: for every (agent, target) FIRST production
   (first TASK_ATTEMPT step by that agent whose product is t): first_in_world
   (no other agent produced t before), transmitted (a prior exposure to a
   recipe for t delivered to this agent, with listener_naive, within
   window_days before the production; record the earliest such utterance_seq
   and speaker = the attributed teacher), else independent. Output the
   per-tier table of {first_in_world, transmitted, independent} and the list
   of transmitted acquisitions (student, teacher, target, tier, utterance_seq,
   production_seq).
   - classes are exclusive in that priority; "before" = utterance_seq <
     production_seq and production_day - utterance_day <= window_days;
     ``classify_acquisitions`` is the pure, reusable function.

R6 Assertions (misinformation): a DECLARATIVE sentence in a CONVERSE or NOTE
   text that names a pair (a,b) and a product t and contains a yield verb
   (yield|yields|yielded|give|gives|gave|make|makes|made|produce|produces|
   produced|birth|births|birthed|->|→|=) is an assertion (a,b)->t; exclude
   sentences containing '?' and sentences whose first word is one of
   if/whether/shall/should/could/would/might/perhaps/maybe/suppose/let/try/
   test/measure/unless. Truth: the global recipe map at END of run (any
   agent's journal): correct if map[(a,b)]==t, false if map has (a,b) with a
   different product (incl. slag — slag only appears as a product, so a pair
   known to be slag with asserted t != slag is false), unknown if the pair was
   never executed by anyone. Report counts by channel (converse/note) and by
   truth, per agent, and replication: a false assertion repeated (same a,b,t)
   by a DIFFERENT agent within 3 days of the original.
   - AS IMPLEMENTED (tightened — the sentence-level cross product of every
     named pair with every named product mislabels enumerations such as
     "fire+wood→murmuring-smoke, earth+water→burnished-iron"): mentions are
     bases, compounds and the literal ``slag`` in text order. A PAIR is two
     consecutive ingredient mentions (bases/compounds) with different names
     whose gap is only pair connectors (``+ - / &`` and/with/plus/paired
     with/together with/against, any whitespace), taken left to right
     without overlap and deduped. Product mentions are the compounds not in
     any pair (deduped, first-mention order) plus every ``slag`` mention.
     If every product mention is slag, every pair asserts slag; else if the
     pair and product counts are equal they are zipped in order; any other
     sentence that passed the declarative/yield filter and mentions two or
     more ingredients is counted as ``ambiguous`` and asserts nothing.
   - declarative: no '?' anywhere in the sentence; first word = first
     ``\\w+`` run, lowercased; yield verbs match word-bounded,
     case-insensitively, the three symbols anywhere;
   - replication counts later false assertions of the same (a,b,t) by an
     agent other than the earliest asserter, with day - original day <= 3.

R7 Rendering (optional, only if llm_texts exists for the run): for each
   exposure, rendered = the utterance text's first 80 chars appear in any
   later tick prompt of the listener on the same or next day (llm_texts rows
   keyed by LLM_CALL seq; LLM_CALL payload has agent and purpose). Report
   rendered permille; null when llm_texts is absent.
   - "later" = LLM_CALL seq > utterance seq, purpose "tick", actor =
     listener, event day in {day, day + 1}; the needle is searched in the
     decoded ``user`` field of the canonical-JSON prompt envelope (the raw
     prompt when it does not decode to an object with a str ``user``).

exposure.json layout: ``run_id``, ``window_days``, ``pair_rule``,
``utterances`` {total, naming_any_compound, naming_known_pair and
naming_known_pair_and_product (the "sentence" rule, whatever ``pair_rule``
is), naming_connected_pair and naming_connected_pair_and_product (the
"connected" rule, likewise), distinct_recipes_spoken (pair AND product in one
sentence) and distinct_known_pairs_spoken (both under the selected rule)},
``exposures`` {total, naive, rendered_permille
(null without llm_texts), by_tier: {tier: {naive, uptake, uptake_permille}}},
``control`` {pairs, uptake, uptake_permille}, ``acquisitions`` {by_tier:
{tier: {first_in_world, transmitted, independent}}, transmitted: [{student,
teacher, target, tier, utterance_seq, utterance_day, production_seq,
production_day}]}, ``assertions`` {converse: {total, correct, false, unknown,
ambiguous}, note: {...}, false_replicated, per_agent: {agent: {asserted,
false}}, examples_false: first 10 by seq of {seq, agent, channel, a, b,
asserted, truth}}, ``per_agent`` {agent: {utterances, exposures_given,
exposures_received, uptakes_caused, uptakes_taken}}, ``examples``
{top_recipe_utterances: 5 utterances by (-distinct recipes, seq), each {seq,
day, speaker, recipes}}. Tier keys are strings.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast, get_args

from lamarck.contracts import EventKind, EventRecord, LiveWorldConfig
from lamarck.engine import load_live_config
from lamarck.eventstore import EventStore
from lamarck.universes import WuxingUniverse
from lamarck.universes.wuxing import BASES, SLAG

__all__ = [
    "Acquisition",
    "Assertion",
    "ExposureRecord",
    "KnowledgeFold",
    "Mention",
    "NameMatcher",
    "PairRule",
    "Production",
    "RecipeNames",
    "classify_acquisitions",
    "exposure_records",
    "find_assertions",
    "fold_exposure",
    "match_names",
    "names_from_config",
    "names_from_events",
    "split_sentences",
    "write_exposure",
]

PairRule = Literal["connected", "sentence"]
"""R3's pair rule: connector-joined mentions (the frozen rule) or every pair
of names in a sentence (the original minimal rule)."""

DEFAULT_PAIR_RULE: PairRule = "connected"
PAIR_RULES: tuple[str, ...] = get_args(PairRule)
DEFAULT_WINDOW_DAYS = 2
REPLICATION_WINDOW_DAYS = 3  # R6: a false assertion repeated within 3 days
RENDER_PREFIX_CHARS = 80  # R7: the utterance prefix searched in prompts
TOP_UTTERANCES = 5
FALSE_EXAMPLES = 10

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;:\n])")
_FIRST_WORD = re.compile(r"\w+")
_HEDGES = frozenset(
    {
        "if",
        "whether",
        "shall",
        "should",
        "could",
        "would",
        "might",
        "perhaps",
        "maybe",
        "suppose",
        "let",
        "try",
        "test",
        "measure",
        "unless",
    }
)
_YIELD = re.compile(
    r"\b(?:yield|yields|yielded|give|gives|gave|make|makes|made"
    r"|produce|produces|produced|birth|births|birthed)\b|->|→|=",
    re.IGNORECASE,
)
_CONNECTOR_GAP = re.compile(
    r"^(?:\s*(?:\+|-|/|&|and|with|plus|paired with|together with|against)\s*)+$",
    re.IGNORECASE,
)
_BOARD_LINE = re.compile(r"The board pays \d+ stones for (\S+) \(([^)]+)\)\.")


# ------------------------------------------------------------------ names


@dataclass(frozen=True)
class RecipeNames:
    """The names the matcher looks for: bases (word-bounded) and compounds
    (whole hyphenated tokens), plus each compound's tier when known (0
    otherwise). Build with ``names_from_config`` / ``names_from_events`` or
    by hand in tests."""

    bases: tuple[str, ...]
    compounds: tuple[str, ...]
    tiers: Mapping[str, int] = field(default_factory=dict)

    def tier_of(self, name: str) -> int:
        return int(self.tiers.get(name, 0))


def _title_target(title: str) -> str:
    """The compound named by a public task title ``produce "X"``."""
    parts = title.split('"')
    return parts[1] if len(parts) >= 3 else title


def names_from_config(cfg: LiveWorldConfig) -> RecipeNames:
    """Rebuild the run's universe from its config and list every compound
    (with tier) — the same construction the live runner uses."""
    if cfg.universe.name != "wuxing":
        raise ValueError(f"unknown universe {cfg.universe.name!r}; only 'wuxing' is supported")
    universe = WuxingUniverse(cfg.universe.seed, cfg.universe.tiers)
    tiers: dict[str, int] = {}
    for tier in range(1, cfg.universe.tiers + 1):
        for stub in universe.tasks(tier):
            tiers[_title_target(stub.title)] = stub.tier
    return RecipeNames(bases=tuple(BASES), compounds=tuple(sorted(tiers)), tiers=tiers)


def _tiers_from_log(events: Iterable[EventRecord]) -> dict[str, int]:
    """compound -> tier from TASK_ATTEMPT board sentences joined with the
    attempt's claims (the log-only fallback for R3's tier)."""
    tiers: dict[str, int] = {}
    for ev in events:
        if ev.kind is not EventKind.TASK_ATTEMPT:
            continue
        claim_tier: dict[str, int] = {}
        claims = ev.payload.get("claims")
        for claim in claims if isinstance(claims, list) else []:
            if isinstance(claim, dict):
                claim_tier[str(claim.get("task_id", ""))] = int(claim.get("tier", 0))
        message = ev.payload.get("message")
        for name, task_id in _BOARD_LINE.findall(message if isinstance(message, str) else ""):
            if name not in tiers and task_id in claim_tier:
                tiers[name] = claim_tier[task_id]
    return tiers


def names_from_events(events: list[EventRecord]) -> RecipeNames:
    """Fallback without a config: bases are the wuxing bases; compounds are
    every non-slag product or non-base ingredient seen in a TASK_ATTEMPT."""
    seen: set[str] = set()
    for ev in events:
        if ev.kind is not EventKind.TASK_ATTEMPT:
            continue
        for (a, b), product in _executed_steps(ev.payload):
            seen.update((a, b, product))
    compounds = sorted(name for name in seen if name not in BASES and name != SLAG)
    return RecipeNames(
        bases=tuple(BASES), compounds=tuple(compounds), tiers=_tiers_from_log(events)
    )


# ---------------------------------------------------------------- matcher


class Mention(NamedTuple):
    """One name occurrence in a sentence (``kind`` is base/compound/slag)."""

    start: int
    end: int
    name: str
    kind: str


def _alternation(names: Iterable[str], before: str, after: str) -> re.Pattern[str] | None:
    body = "|".join(re.escape(n.lower()) for n in sorted(set(names), key=lambda s: (-len(s), s)))
    return re.compile(f"{before}(?:{body}){after}", re.IGNORECASE) if body else None


class NameMatcher:
    """R2's name matcher: bases word-bounded, compounds (and ``slag``) as
    whole hyphenated tokens, all case-insensitive; returns canonical names."""

    def __init__(self, names: RecipeNames) -> None:
        self._canonical = {n.lower(): n for n in (*names.bases, *names.compounds)}
        self.bases: frozenset[str] = frozenset(names.bases)
        """Canonical base names (R3's BASES for ``listener_can_make``)."""
        self._base_re = _alternation(names.bases, r"\b", r"\b")
        self._token_re = _alternation((*names.compounds, SLAG), r"(?<![\w-])", r"(?![\w-])")

    def mentions(self, text: str) -> list[Mention]:
        """Every mention in text order; base mentions inside a compound
        mention's span are dropped."""
        found: list[Mention] = []
        if self._token_re is not None:
            for m in self._token_re.finditer(text):
                low = m.group(0).lower()
                kind = "slag" if low == SLAG else "compound"
                found.append(Mention(m.start(), m.end(), self._canonical.get(low, low), kind))
        if self._base_re is not None:
            for m in self._base_re.finditer(text):
                low = m.group(0).lower()
                found.append(Mention(m.start(), m.end(), self._canonical[low], "base"))
        found.sort(key=lambda x: (x.start, -(x.end - x.start)))
        kept: list[Mention] = []
        for mention in found:
            if kept and mention.start < kept[-1].end:
                continue  # nested inside the previous (longer) mention
            kept.append(mention)
        return kept

    def names(self, text: str) -> list[str]:
        """Distinct ingredient names (bases and compounds, never slag) in
        first-occurrence order."""
        return _distinct_names(self.mentions(text))


def _distinct_names(mentions: Iterable[Mention]) -> list[str]:
    """Distinct ingredient names of *mentions*, first-occurrence order."""
    out: list[str] = []
    for mention in mentions:
        if mention.kind != "slag" and mention.name not in out:
            out.append(mention.name)
    return out


def split_sentences(text: str) -> list[str]:
    """R2's split: on ``. ! ? ; :`` and newline, terminator kept, stripped,
    empties dropped."""
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def match_names(sentence: str, names: RecipeNames) -> list[str]:
    """Distinct base/compound names a sentence mentions (R2), first-occurrence
    order. Convenience wrapper over ``NameMatcher``."""
    return NameMatcher(names).names(sentence)


def _pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _named_pairs(names: list[str]) -> list[tuple[str, str]]:
    """All unordered pairs of distinct names (R3's "sentence" pair rule)."""
    return [_pair_key(a, b) for a, b in combinations(names, 2)]


def _check_pair_rule(pair_rule: str) -> PairRule:
    """Runtime guard for the ``Literal`` (callers may pass any string)."""
    if pair_rule not in PAIR_RULES:
        raise ValueError(f"unknown pair_rule {pair_rule!r}; expected one of {PAIR_RULES}")
    return cast(PairRule, pair_rule)


# ------------------------------------------------------------- assertions


class Assertion(NamedTuple):
    """One R6 assertion ``(a,b) -> asserted`` made in a converse or note."""

    seq: int
    day: int
    agent: str
    channel: str
    a: str
    b: str
    asserted: str


def _is_declarative(sentence: str) -> bool:
    if "?" in sentence:
        return False
    first = _FIRST_WORD.search(sentence)
    return first is None or first.group(0).lower() not in _HEDGES


def _connected_pairs(sentence: str, mentions: list[Mention]) -> list[tuple[str, str]]:
    """Consecutive ingredient mentions joined only by pair connectors, taken
    left to right without overlap, deduped."""
    ingredients = [m for m in mentions if m.kind != "slag"]
    pairs: list[tuple[str, str]] = []
    i = 0
    while i + 1 < len(ingredients):
        left, right = ingredients[i], ingredients[i + 1]
        gap = sentence[left.end : right.start]
        if left.name != right.name and _CONNECTOR_GAP.match(gap):
            key = _pair_key(left.name, right.name)
            if key not in pairs:
                pairs.append(key)
            i += 2
        else:
            i += 1
    return pairs


def find_assertions(sentence: str, matcher: NameMatcher) -> tuple[list[tuple[str, str, str]], bool]:
    """R6's sentence parse: ``(triples, ambiguous)`` where triples are
    ``(a, b, asserted_product)`` and ``ambiguous`` flags a declarative yield
    sentence naming two or more ingredients that produced no triple."""
    if not _is_declarative(sentence) or not _YIELD.search(sentence):
        return [], False
    mentions = matcher.mentions(sentence)
    distinct = {m.name for m in mentions if m.kind != "slag"}
    if len(distinct) < 2:
        return [], False
    pairs = _connected_pairs(sentence, mentions)
    in_pair = {name for pair in pairs for name in pair}
    products: list[str] = []
    for m in mentions:
        if m.kind == "slag":
            products.append(SLAG)
        elif m.kind == "compound" and m.name not in in_pair and m.name not in products:
            products.append(m.name)
    if not pairs or not products:
        return [], True
    if all(p == SLAG for p in products):
        return [(a, b, SLAG) for a, b in pairs], False
    if len(pairs) == len(products):
        return [(a, b, t) for (a, b), t in zip(pairs, products, strict=True)], False
    return [], True


# ------------------------------------------------------------ world fold


class StepResult(NamedTuple):
    """One executed TASK_ATTEMPT step as folded for its actor."""

    pair: tuple[str, str]
    product: str
    pair_new: bool  # the actor had never executed this pair before
    product_new: bool  # the actor had never produced this (non-slag) product


def _executed_steps(payload: Mapping[str, Any]) -> list[tuple[tuple[str, str], str]]:
    """``(sorted pair, product)`` for the executed prefix of an attempt."""
    steps = payload.get("steps")
    products = payload.get("step_products")
    if not isinstance(steps, list) or not isinstance(products, list):
        return []
    out: list[tuple[tuple[str, str], str]] = []
    for step, product in zip(steps, products, strict=False):
        if (
            not isinstance(step, list)
            or len(step) != 2
            or not all(isinstance(x, str) for x in step)
            or not isinstance(product, str)
        ):
            break
        out.append((_pair_key(step[0], step[1]), product))
    return out


class KnowledgeFold:
    """Location, liveness, journal and satchel per agent (R1 + delivery).

    Mirrors ``WorldStateFold``'s rules without needing a config: agents spawn
    at ``spawn_location``, non-degraded travel moves them, a non-degraded
    converse is heard by every OTHER living agent at the speaker's location
    at emission. Journal and satchel follow the lab-journal and satchel rules
    exactly (the tests assert equality with ``WorldStateFold``).
    """

    def __init__(self, spawn_location: str = "") -> None:
        self._spawn_location = spawn_location
        self.spawn_order: list[str] = []
        self._location: dict[str, str] = {}
        self._alive: dict[str, bool] = {}
        self._journal: dict[str, list[tuple[str, str, str]]] = {}
        self._journal_index: dict[str, dict[tuple[str, str], str]] = {}
        self._satchel: dict[str, list[str]] = {}

    def apply(self, ev: EventRecord) -> list[StepResult]:
        """Fold one event; TASK_ATTEMPTs return their executed steps."""
        if ev.kind is EventKind.AGENT_SPAWNED:
            agent = str(ev.payload.get("agent_id", ev.actor))
            if agent not in self._location:
                self.spawn_order.append(agent)
                self._location[agent] = self._spawn_location
                self._alive[agent] = True
                self._journal[agent] = []
                self._journal_index[agent] = {}
                self._satchel[agent] = []
        elif ev.kind is EventKind.AGENT_DIED:
            self._alive[ev.actor] = False
        elif ev.kind is EventKind.ACTION:
            to = ev.payload.get("to")
            if (
                ev.payload.get("type") == "travel"
                and not ev.payload.get("degraded")
                and isinstance(to, str)
                and ev.actor in self._location
            ):
                self._location[ev.actor] = to
        elif ev.kind is EventKind.TASK_ATTEMPT and ev.actor in self._location:
            return [self._record_step(ev.actor, pair, p) for pair, p in _executed_steps(ev.payload)]
        return []

    def _record_step(self, agent: str, pair: tuple[str, str], product: str) -> StepResult:
        index = self._journal_index[agent]
        pair_new = pair not in index
        if pair_new:
            index[pair] = product
            self._journal[agent].append((pair[0], pair[1], product))
        product_new = product != SLAG and product not in self._satchel[agent]
        if product_new:
            self._satchel[agent].append(product)
        return StepResult(pair, product, pair_new, product_new)

    def known(self, agent: str) -> bool:
        return agent in self._location

    def alive(self, agent: str) -> bool:
        return self._alive.get(agent, False)

    def location(self, agent: str) -> str:
        return self._location[agent]

    def listeners(self, speaker: str) -> list[str]:
        """Other living agents at the speaker's location, spawn order."""
        here = self._location[speaker]
        return [
            other
            for other in self.spawn_order
            if other != speaker and self._alive[other] and self._location[other] == here
        ]

    def journal(self, agent: str) -> list[tuple[str, str, str]]:
        return list(self._journal[agent])

    def journal_product(self, agent: str, pair: tuple[str, str]) -> str | None:
        return self._journal_index[agent].get(pair)

    def satchel(self, agent: str) -> list[str]:
        return list(self._satchel[agent])


# ---------------------------------------------------------------- records


@dataclass
class ExposureRecord:
    """One R3 exposure (utterance × recipe × listener) with its R4 uptake and
    R7 rendering fields (None until computed / when not applicable)."""

    utterance_seq: int
    day: int
    speaker: str
    listener: str
    a: str
    b: str
    target: str
    tier: int
    product_named: bool
    listener_naive: bool
    listener_can_make: bool
    uptake_seq: int | None = None
    uptake_delay_days: int | None = None
    produced_target: bool | None = None
    rendered: bool | None = None

    @property
    def pair(self) -> tuple[str, str]:
        return (self.a, self.b)

    def as_dict(self) -> dict[str, Any]:
        return dict(vars(self))


class Production(NamedTuple):
    """An agent's FIRST production of a target (R5 input)."""

    agent: str
    target: str
    seq: int
    day: int


class Acquisition(NamedTuple):
    """R5 output: how an agent came to hold a target for the first time."""

    student: str
    target: str
    tier: int
    production_seq: int
    production_day: int
    kind: str  # first_in_world | transmitted | independent
    teacher: str | None
    utterance_seq: int | None
    utterance_day: int | None


def classify_acquisitions(
    productions: Iterable[Production],
    exposures: Iterable[ExposureRecord],
    window_days: int,
    tiers: Mapping[str, int] | None = None,
) -> list[Acquisition]:
    """R5, as a pure function (reusable by the runner-side rule).

    ``productions`` are first productions per (agent, target); ``exposures``
    are R3 records (any order). Priority: first_in_world (the earliest
    production of the target world-wide is this agent's), else transmitted
    (earliest naive exposure to a recipe for the target delivered to the
    agent with utterance_seq < production_seq and production_day -
    utterance_day <= window_days), else independent. Output in production
    seq order."""
    ordered = sorted(productions, key=lambda p: p.seq)
    world_first: dict[str, str] = {}
    for p in ordered:
        world_first.setdefault(p.target, p.agent)
    by_student: dict[tuple[str, str], list[ExposureRecord]] = {}
    for e in sorted(exposures, key=lambda e: e.utterance_seq):
        if e.listener_naive:
            by_student.setdefault((e.listener, e.target), []).append(e)
    out: list[Acquisition] = []
    for p in ordered:
        tier = int(tiers.get(p.target, 0)) if tiers else 0
        if world_first[p.target] == p.agent:
            out.append(
                Acquisition(
                    p.agent, p.target, tier, p.seq, p.day, "first_in_world", None, None, None
                )
            )
            continue
        lesson = next(
            (
                e
                for e in by_student.get((p.agent, p.target), [])
                if e.utterance_seq < p.seq and p.day - e.day <= window_days
            ),
            None,
        )
        if lesson is None:
            out.append(
                Acquisition(p.agent, p.target, tier, p.seq, p.day, "independent", None, None, None)
            )
        else:
            out.append(
                Acquisition(
                    p.agent,
                    p.target,
                    tier,
                    p.seq,
                    p.day,
                    "transmitted",
                    lesson.speaker,
                    lesson.utterance_seq,
                    lesson.day,
                )
            )
    return out


# ------------------------------------------------------------------- fold


_Recipes = list[tuple[str, str, str, bool]]  # (a, b, t, product_named), deduped


class _Utterance(NamedTuple):
    seq: int
    day: int
    speaker: str
    text: str
    any_compound: bool
    recipes: _Recipes  # under the selected pair rule (drives the records)
    sentence_recipes: _Recipes  # under the "sentence" rule (always measured)
    connected_recipes: _Recipes  # under the "connected" rule (always measured)


@dataclass
class _State:
    run_id: str = ""
    window_days: int = DEFAULT_WINDOW_DAYS
    pair_rule: PairRule = DEFAULT_PAIR_RULE
    spawn_order: list[str] = field(default_factory=list)
    utterances: list[_Utterance] = field(default_factory=list)
    exposures: list[ExposureRecord] = field(default_factory=list)
    assertions: list[Assertion] = field(default_factory=list)
    ambiguous: dict[str, int] = field(default_factory=lambda: {"converse": 0, "note": 0})
    productions: list[Production] = field(default_factory=list)
    acquisitions: list[Acquisition] = field(default_factory=list)
    recipe_map: dict[tuple[str, str], str] = field(default_factory=dict)
    tiers: dict[str, int] = field(default_factory=dict)
    first_exec: dict[tuple[str, tuple[str, str]], tuple[int, int]] = field(default_factory=dict)
    control_candidates: list[tuple[str, int, tuple[str, str]]] = field(default_factory=list)
    control_pairs: int = 0
    control_uptake: int = 0
    has_prompts: bool = False
    tick_prompts: dict[tuple[str, int], list[tuple[int, str]]] = field(default_factory=dict)


def _utterance_recipes(
    text: str, speaker: str, world: KnowledgeFold, matcher: NameMatcher
) -> tuple[bool, _Recipes, _Recipes]:
    """R3's speaker-side test: ``(names any compound, sentence-rule recipes,
    connected-rule recipes)`` where each recipe is ``(a, b, t,
    product_named)`` deduped across sentences. Both pair rules are folded in
    one pass so the summary can report them side by side."""
    any_compound = False
    by_sentence: dict[tuple[str, str, str], bool] = {}
    by_connector: dict[tuple[str, str, str], bool] = {}
    for sentence in split_sentences(text):
        mentions = matcher.mentions(sentence)
        names = _distinct_names(mentions)
        if any(n not in matcher.bases for n in names):
            any_compound = True
        for pairs, recipes in (
            (_named_pairs(names), by_sentence),
            (_connected_pairs(sentence, mentions), by_connector),
        ):
            for a, b in pairs:
                t = world.journal_product(speaker, (a, b))
                if t is None or t == SLAG:
                    continue
                named = t in names
                recipes[(a, b, t)] = recipes.get((a, b, t), False) or named
    return (
        any_compound,
        [(a, b, t, named) for (a, b, t), named in by_sentence.items()],
        [(a, b, t, named) for (a, b, t), named in by_connector.items()],
    )


def _user_text(prompt: str) -> str:
    try:
        envelope = json.loads(prompt)
    except ValueError:
        return prompt
    if isinstance(envelope, dict) and isinstance(envelope.get("user"), str):
        return str(envelope["user"])
    return prompt


def _fold(
    events: list[EventRecord],
    names: RecipeNames,
    *,
    spawn_location: str,
    window_days: int,
    pair_rule: PairRule,
    prompts: Mapping[int, str] | None,
) -> _State:
    """Pass 1 (seq order) then the post-passes; see the module docstring."""
    st = _State(
        window_days=window_days,
        pair_rule=_check_pair_rule(pair_rule),
        has_prompts=prompts is not None,
    )
    st.tiers = dict(names.tiers)
    for name, tier in _tiers_from_log(events).items():
        st.tiers.setdefault(name, tier)
    matcher = NameMatcher(names)
    bases = matcher.bases
    world = KnowledgeFold(spawn_location)
    produced: set[tuple[str, str]] = set()

    for ev in events:
        if ev.kind is EventKind.RUN_STARTED:
            st.run_id = str(ev.payload.get("run_id", ""))
        elif ev.kind is EventKind.DAY_STARTED:
            _snapshot_control(st, world, bases, ev.day)
        elif ev.kind is EventKind.TASK_ATTEMPT:
            for step in world.apply(ev):
                st.recipe_map.setdefault(step.pair, step.product)
                if step.pair_new:
                    st.first_exec[(ev.actor, step.pair)] = (ev.seq, ev.day)
                if step.product_new and (ev.actor, step.product) not in produced:
                    produced.add((ev.actor, step.product))
                    st.productions.append(Production(ev.actor, step.product, ev.seq, ev.day))
        elif ev.kind is EventKind.ACTION:
            world.apply(ev)
            _fold_action(st, ev, world, matcher, bases)
        elif ev.kind is EventKind.LLM_CALL:
            if prompts is not None and ev.payload.get("purpose") == "tick":
                prompt = prompts.get(ev.seq)
                if prompt is not None:
                    key = (ev.actor, ev.day)
                    st.tick_prompts.setdefault(key, []).append((ev.seq, _user_text(prompt)))
        else:
            world.apply(ev)
    st.spawn_order = list(world.spawn_order)
    _post_uptake(st)
    _post_control(st)
    st.acquisitions = classify_acquisitions(st.productions, st.exposures, window_days, st.tiers)
    if prompts is not None:
        _post_rendered(st)
    return st


def _snapshot_control(st: _State, world: KnowledgeFold, bases: frozenset[str], day: int) -> None:
    """R4 control: every untried makeable pair per living agent at day start."""
    for agent in world.spawn_order:
        if not world.alive(agent):
            continue
        makeable = sorted(set(bases) | set(world.satchel(agent)))
        for a, b in combinations(makeable, 2):
            if world.journal_product(agent, _pair_key(a, b)) is None:
                st.control_candidates.append((agent, day, _pair_key(a, b)))


def _fold_action(
    st: _State, ev: EventRecord, world: KnowledgeFold, matcher: NameMatcher, bases: frozenset[str]
) -> None:
    if ev.payload.get("degraded") or not world.known(ev.actor):
        return
    atype = ev.payload.get("type")
    text = ev.payload.get("text")
    if atype not in ("converse", "note") or not isinstance(text, str):
        return
    channel = str(atype)
    for sentence in split_sentences(text):
        triples, ambiguous = find_assertions(sentence, matcher)
        st.ambiguous[channel] += 1 if ambiguous else 0
        st.assertions.extend(
            Assertion(ev.seq, ev.day, ev.actor, channel, a, b, t) for a, b, t in triples
        )
    if channel != "converse":
        return
    any_compound, by_sentence, by_connector = _utterance_recipes(text, ev.actor, world, matcher)
    recipes = by_connector if st.pair_rule == "connected" else by_sentence
    st.utterances.append(
        _Utterance(ev.seq, ev.day, ev.actor, text, any_compound, recipes, by_sentence, by_connector)
    )
    listeners = world.listeners(ev.actor)
    for a, b, t, named in recipes:
        for listener in listeners:
            satchel = set(world.satchel(listener))
            st.exposures.append(
                ExposureRecord(
                    utterance_seq=ev.seq,
                    day=ev.day,
                    speaker=ev.actor,
                    listener=listener,
                    a=a,
                    b=b,
                    target=t,
                    tier=int(st.tiers.get(t, 0)),
                    product_named=named,
                    listener_naive=world.journal_product(listener, (a, b)) is None,
                    listener_can_make=all(x in bases or x in satchel for x in (a, b)),
                )
            )


def _post_uptake(st: _State) -> None:
    """R4 uptake per naive exposure."""
    for e in st.exposures:
        if not e.listener_naive:
            continue
        first = st.first_exec.get((e.listener, e.pair))
        if first is None:
            continue
        seq, day = first
        if seq > e.utterance_seq and day - e.day <= st.window_days:
            e.uptake_seq = seq
            e.uptake_delay_days = day - e.day
            e.produced_target = st.recipe_map.get(e.pair) == e.target


def _post_control(st: _State) -> None:
    """R4 control: candidates minus exposed pairs; first-tries within window."""
    exposed_days: dict[tuple[str, tuple[str, str]], list[int]] = {}
    for e in st.exposures:
        exposed_days.setdefault((e.listener, e.pair), []).append(e.day)
    w = st.window_days
    for agent, day, pair in st.control_candidates:
        if any(abs(d - day) <= w for d in exposed_days.get((agent, pair), [])):
            continue
        st.control_pairs += 1
        first = st.first_exec.get((agent, pair))
        if first is not None and 0 <= first[1] - day <= w:
            st.control_uptake += 1


def _post_rendered(st: _State) -> None:
    """R7: the utterance prefix appears in a later tick prompt of the
    listener on the same or next day (cached per utterance × listener)."""
    text_of = {u.seq: u.text for u in st.utterances}
    cache: dict[tuple[int, str], bool] = {}
    for e in st.exposures:
        key = (e.utterance_seq, e.listener)
        if key not in cache:
            needle = text_of[e.utterance_seq][:RENDER_PREFIX_CHARS]
            later = [
                *st.tick_prompts.get((e.listener, e.day), []),
                *st.tick_prompts.get((e.listener, e.day + 1), []),
            ]
            cache[key] = any(seq > e.utterance_seq and needle in user for seq, user in later)
        e.rendered = cache[key]


# --------------------------------------------------------------- summary


def _permille(numerator: int, denominator: int) -> int:
    return numerator * 1000 // denominator if denominator else 0


def _truth(st: _State, a: Assertion) -> str:
    actual = st.recipe_map.get((a.a, a.b))
    if actual is None:
        return "unknown"
    return "correct" if actual == a.asserted else "false"


def _summarize_assertions(st: _State) -> dict[str, Any]:
    truths = [(a, _truth(st, a)) for a in st.assertions]
    by_channel: dict[str, dict[str, int]] = {}
    for channel in ("converse", "note"):
        counts = {"total": 0, "correct": 0, "false": 0, "unknown": 0}
        for a, truth in truths:
            if a.channel == channel:
                counts["total"] += 1
                counts[truth] += 1
        counts["ambiguous"] = st.ambiguous[channel]
        by_channel[channel] = counts
    false_ones = [a for a, truth in truths if truth == "false"]
    originals: dict[tuple[str, str, str], Assertion] = {}
    for a in false_ones:  # seq order
        originals.setdefault((a.a, a.b, a.asserted), a)
    replicated = 0
    for a in false_ones:
        original = originals[(a.a, a.b, a.asserted)]
        if a.agent != original.agent and a.day - original.day <= REPLICATION_WINDOW_DAYS:
            replicated += 1
    per_agent = {agent: {"asserted": 0, "false": 0} for agent in st.spawn_order}
    for a, truth in truths:
        entry = per_agent.setdefault(a.agent, {"asserted": 0, "false": 0})
        entry["asserted"] += 1
        entry["false"] += 1 if truth == "false" else 0
    examples = [
        {
            "seq": a.seq,
            "agent": a.agent,
            "channel": a.channel,
            "a": a.a,
            "b": a.b,
            "asserted": a.asserted,
            "truth": st.recipe_map[(a.a, a.b)],
        }
        for a in false_ones[:FALSE_EXAMPLES]
    ]
    return {
        "converse": by_channel["converse"],
        "note": by_channel["note"],
        "false_replicated": replicated,
        "per_agent": per_agent,
        "examples_false": examples,
    }


def _summarize_exposures(st: _State) -> dict[str, Any]:
    by_tier: dict[str, dict[str, int]] = {}
    for e in st.exposures:
        if not e.listener_naive:
            continue
        row = by_tier.setdefault(str(e.tier), {"naive": 0, "uptake": 0})
        row["naive"] += 1
        row["uptake"] += 1 if e.uptake_seq is not None else 0
    for row in by_tier.values():
        row["uptake_permille"] = _permille(row["uptake"], row["naive"])
    rendered: int | None = None
    if st.has_prompts:
        rendered = _permille(sum(1 for e in st.exposures if e.rendered), len(st.exposures))
    return {
        "total": len(st.exposures),
        "naive": sum(1 for e in st.exposures if e.listener_naive),
        "rendered_permille": rendered,
        "by_tier": {k: by_tier[k] for k in sorted(by_tier, key=int)},
    }


def _summarize_acquisitions(st: _State) -> dict[str, Any]:
    by_tier: dict[str, dict[str, int]] = {}
    transmitted: list[dict[str, Any]] = []
    for acq in st.acquisitions:
        row = by_tier.setdefault(
            str(acq.tier), {"first_in_world": 0, "transmitted": 0, "independent": 0}
        )
        row[acq.kind] += 1
        if acq.kind == "transmitted":
            transmitted.append(
                {
                    "student": acq.student,
                    "teacher": acq.teacher,
                    "target": acq.target,
                    "tier": acq.tier,
                    "utterance_seq": acq.utterance_seq,
                    "utterance_day": acq.utterance_day,
                    "production_seq": acq.production_seq,
                    "production_day": acq.production_day,
                }
            )
    return {
        "by_tier": {k: by_tier[k] for k in sorted(by_tier, key=int)},
        "transmitted": transmitted,
    }


def _summarize_per_agent(st: _State) -> dict[str, dict[str, int]]:
    per_agent = {
        agent: {
            "utterances": 0,
            "exposures_given": 0,
            "exposures_received": 0,
            "uptakes_caused": 0,
            "uptakes_taken": 0,
        }
        for agent in st.spawn_order
    }
    for u in st.utterances:
        per_agent[u.speaker]["utterances"] += 1
    for e in st.exposures:
        per_agent[e.speaker]["exposures_given"] += 1
        per_agent[e.listener]["exposures_received"] += 1
        if e.uptake_seq is not None:
            per_agent[e.speaker]["uptakes_caused"] += 1
            per_agent[e.listener]["uptakes_taken"] += 1
    return per_agent


def _summarize(st: _State) -> dict[str, Any]:
    spoken_full = {(a, b, t) for u in st.utterances for a, b, t, named in u.recipes if named}
    spoken_pairs = {(a, b, t) for u in st.utterances for a, b, t, _ in u.recipes}
    top = sorted(st.utterances, key=lambda u: (-len(u.recipes), u.seq))[:TOP_UTTERANCES]
    return {
        "run_id": st.run_id,
        "window_days": st.window_days,
        "pair_rule": st.pair_rule,
        "utterances": {
            "total": len(st.utterances),
            "naming_any_compound": sum(1 for u in st.utterances if u.any_compound),
            "naming_known_pair": sum(1 for u in st.utterances if u.sentence_recipes),
            "naming_known_pair_and_product": sum(
                1 for u in st.utterances if any(named for *_, named in u.sentence_recipes)
            ),
            "naming_connected_pair": sum(1 for u in st.utterances if u.connected_recipes),
            "naming_connected_pair_and_product": sum(
                1 for u in st.utterances if any(named for *_, named in u.connected_recipes)
            ),
            "distinct_recipes_spoken": len(spoken_full),
            "distinct_known_pairs_spoken": len(spoken_pairs),
        },
        "exposures": _summarize_exposures(st),
        "control": {
            "pairs": st.control_pairs,
            "uptake": st.control_uptake,
            "uptake_permille": _permille(st.control_uptake, st.control_pairs),
        },
        "acquisitions": _summarize_acquisitions(st),
        "assertions": _summarize_assertions(st),
        "per_agent": _summarize_per_agent(st),
        "examples": {
            "top_recipe_utterances": [
                {"seq": u.seq, "day": u.day, "speaker": u.speaker, "recipes": len(u.recipes)}
                for u in top
            ]
        },
    }


# ------------------------------------------------------------- public API


def fold_exposure(
    events: list[EventRecord],
    names: RecipeNames,
    *,
    spawn_location: str = "",
    window_days: int = DEFAULT_WINDOW_DAYS,
    pair_rule: PairRule = DEFAULT_PAIR_RULE,
    prompts: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """The exposure.json dict for *events* (see the module docstring).

    ``spawn_location`` is where AGENT_SPAWNED places agents (the config's
    first location; any constant works when nobody travels back to it);
    ``pair_rule`` selects R3's pair reading ("connected", the default, or
    "sentence"); ``prompts`` maps LLM_CALL seq -> prompt text for R7 (None =
    no ``llm_texts``, rendered_permille null)."""
    return _summarize(
        _fold(
            events,
            names,
            spawn_location=spawn_location,
            window_days=window_days,
            pair_rule=pair_rule,
            prompts=prompts,
        )
    )


def exposure_records(
    events: list[EventRecord],
    names: RecipeNames,
    *,
    spawn_location: str = "",
    window_days: int = DEFAULT_WINDOW_DAYS,
    pair_rule: PairRule = DEFAULT_PAIR_RULE,
    prompts: Mapping[int, str] | None = None,
) -> list[ExposureRecord]:
    """The R3 exposure records under *pair_rule* (with R4/R7 fields
    filled), seq order."""
    return _fold(
        events,
        names,
        spawn_location=spawn_location,
        window_days=window_days,
        pair_rule=pair_rule,
        prompts=prompts,
    ).exposures


def _load_prompts(db_path: Path) -> dict[int, str] | None:
    """Read ``llm_texts`` (seq -> prompt); None when the table is absent.

    SELECT-only, read-only: ``immutable=1`` when the log is checkpointed (no
    ``-wal`` sidecar — nothing is created next to the evidence), ``mode=ro``
    when a WAL is live; never a pragma or a schema statement (``TextsStore``
    would CREATE TABLE and leave an empty table behind on a run without
    prompts). Mirrors ``EventStore(readonly=True)``."""
    wal = db_path.with_name(db_path.name + "-wal")
    query = "mode=ro" if wal.is_file() else "immutable=1"
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?{query}", uri=True)
    try:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'llm_texts'"
        ).fetchone()
        if present is None:
            return None
        rows = conn.execute("SELECT seq, prompt FROM llm_texts ORDER BY seq").fetchall()
    finally:
        conn.close()
    return {int(seq): str(prompt) for seq, prompt in rows}


def write_exposure(
    run_dir: str | Path,
    out_path: str | Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    pair_rule: PairRule = DEFAULT_PAIR_RULE,
) -> Path:
    """Write ``exposure.json`` for *run_dir* (to *out_path* when given) and
    return its path. Names come from ``config.toml`` when present (the
    universe is rebuilt), else from the log; ``llm_texts`` powers R7 when
    present; ``pair_rule`` selects R3's pair reading. Byte-identical across
    re-writes."""
    rd = Path(run_dir)
    db_path = rd / "events.sqlite3"
    with EventStore(db_path, readonly=True) as store:
        events = list(store.scan())
    config_path = rd / "config.toml"
    spawn_location = ""
    if config_path.exists():
        cfg = load_live_config(config_path)
        names = names_from_config(cfg)
        spawn_location = cfg.live.locations[0]
    else:
        names = names_from_events(events)
    data = fold_exposure(
        events,
        names,
        spawn_location=spawn_location,
        window_days=window_days,
        pair_rule=pair_rule,
        prompts=_load_prompts(db_path),
    )
    path = rd / "exposure.json" if out_path is None else Path(out_path)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
