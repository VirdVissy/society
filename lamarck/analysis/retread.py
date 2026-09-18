"""Retread and call-truncation statistics — a deterministic pure fold of a
finished run's event log (Phase-2 locked metrics, Phase-1 baseline).

``write_retread(run_dir, out_path=None)`` reads ``events.sqlite3`` alone
(never the config, never ``llm_texts``, never the clock) and writes
``retread.json`` = ``{run_id, retread, truncation}``. Everything is a pure
function of the event log: ints, strings, bools, lists and dicts only —
never a float (rates are permille ints) — with deterministic ordering
everywhere (agents in spawn order, days ascending, dict keys sorted at
serialization), so two writes over the same log are byte-identical.

Arithmetic conventions (used everywhere below):

- ``permille(num, den)`` = ``1000 * num / den`` rounded HALF-UP to an int
  (``(2000 * num + den) // (2 * den)``); ``0`` when ``den == 0``.
- ``mean(total, count)`` = ``total / count`` floor-divided; ``0`` when
  ``count == 0``.
- percentiles use the NEAREST-RANK method over the sorted sample: ``pP`` is
  the value at 1-based rank ``ceil(N * P / 100)``; ``0`` on an empty sample.

========================================================================
RETREAD (``fold_retread``) — pairs re-tried against the actor's own journal
========================================================================
The lab-journal rule (``lamarck.engine.world_state``, 2026-08-19) gives
every agent a cumulative memory: each EXECUTED step's unordered ingredient
pair with its product. This fold replays exactly that memory per agent and
classifies every executed step against it AS OF BEFORE the step:

- An executed step is step ``i`` of a TASK_ATTEMPT with
  ``i < len(step_products)`` (``step_products`` aligns with the executed
  prefix of ``steps``; an unavailable ingredient halts the attempt).
- The step's pair is ``(min(a, b), max(a, b))`` — order-insensitive.
- ``novel``: the pair is not in the actor's journal; it is then recorded
  with this step's product. ``retread_slag``: the pair is in the journal
  with product ``"slag"``. ``retread_recipe``: the pair is in the journal
  with a non-slag product. ``retread = retread_slag + retread_recipe``.
- Steps fold IN ORDER within one attempt, so a pair repeated inside a single
  attempt counts the second occurrence as a retread against the journal the
  first occurrence just updated.
- ``retread_permille = permille(retread, steps)``.

Per agent (``retread.agents``, spawn order): ``agent_id, name, spawn_day``
(day of AGENT_SPAWNED), the step tally (``steps, novel, retread_slag,
retread_recipe, retread, retread_permille``), ``attempts`` (TASK_ATTEMPT
count), ``attempts_empty`` (attempts with zero executed steps),
``attempts_zero_novel`` (attempts with >= 1 executed step and zero novel
steps — pure retread), ``max_repeats_of_one_pair`` = ``{count, pair}``: the
most times ONE pair was executed by that agent, first try included (ties:
lexicographically smallest pair; ``{count: 0, pair: []}`` with no steps),
and ``by_life_day``: a DENSE series over the agent's lived days — one row
per life-day ``day - spawn_day`` (created at spawn and at every DAY_STARTED
while alive; no rows after AGENT_DIED), each a step tally plus
``life_day`` — so a successor spawned mid-run compares to a founder at the
same age.

``retread.population``: the same tally and attempt counters summed over
all agents plus ``agents`` (count). ``retread.retread_by_day``: a dense,
ascending series over every DAY_STARTED day of ``{day, tally}``.
``retread.known_slag_retries_top``: the ``KNOWN_SLAG_TOP_N`` (10) largest
``{agent_id, pair, count}`` where ``count`` is the number of
``retread_slag`` steps of that agent on that pair — sorted by count
descending, then agent spawn order, then pair.

========================================================================
TRUNCATION (``fold_truncation``) — max_tokens hits and retry cost
========================================================================
Over LLM_CALL events: a call is ``truncated`` iff
``usage_out >= max_tokens`` (both from its own payload). Tick calls group
by ``(actor, day, tick)``: the runner commits a slot's primary LLM_CALL,
then its (at most one) retry LLM_CALL, then the slot's single ACTION, all
at the same tick, so the group size is the number of model calls that slot
cost. Reflection calls (dusk) never retry and share no tick with a slot.

- ``by_purpose[purpose]`` (keys ``"tick"`` and ``"reflection"`` always
  present, others as seen): ``calls, truncated, truncated_permille``,
  ``usage_out_p50 / usage_out_p90 / usage_out_max`` over the NON-truncated
  calls only (nearest rank), and ``max_tokens_seen`` (sorted distinct).
- ``slots``: ``actions`` (ACTION events); ``actions_no_call`` (group size
  0 — the "spent" pre-check skip); ``actions_single_call``;
  ``actions_with_retry`` (group size >= 2) and ``retry_permille`` over
  ``actions``; ``actions_with_retry_after_truncation`` (group size >= 2
  whose FIRST tick call was truncated); ``malformed_forfeits`` (ACTIONs
  with ``degraded`` and ``reason == "malformed"``).
- ``qi``: ``qi_thinking_total`` (sum of ``-qi_delta`` over every LLM_CALL,
  tick and reflection); ``truncated_then_retried_calls`` and
  ``qi_on_truncated_first_calls`` (count / summed ``-qi_delta`` of tick
  calls that were truncated AND followed by another tick call in the same
  group — the bill for output that was thrown away); ``share_permille`` of
  that over ``qi_thinking_total``; ``qi_on_retry_calls`` (summed
  ``-qi_delta`` of every second-or-later tick call in a group).
- ``agents`` (spawn order): ``agent_id, name, alive, spawn_day, death_day``
  (``-1`` while alive), ``calls, usage_in, mean_usage_in`` (all purposes),
  ``tick_calls, usage_in_tick, mean_usage_in_tick``, ``qi_thinking``
  (LLM_CALL bills), ``qi_spent`` (every negative ``qi_delta`` on the
  agent's events, as in report.json), ``days_active`` (distinct days with
  at least one LLM_CALL or ACTION by the agent) and
  ``qi_per_day = mean(qi_spent, days_active)`` — lifespan against prompt
  length.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from lamarck.contracts import EventKind, EventRecord
from lamarck.eventstore import EventStore
from lamarck.universes.wuxing import SLAG

__all__ = ["KNOWN_SLAG_TOP_N", "fold_retread", "fold_truncation", "write_retread"]

KNOWN_SLAG_TOP_N = 10  # rows in retread.known_slag_retries_top
NO_DEATH = -1  # truncation.agents[].death_day while alive

_Pair = tuple[str, str]
_SlotKey = tuple[str, int, int]  # (actor, day, tick)

# --------------------------------------------------------------- arithmetic


def _ratio(num: int, den: int, scale: int) -> int:
    """``scale * num // den`` (floor — the package-wide convention shared with
    ``exposure`` and ``lifespan``); ``0`` when ``den <= 0``."""
    if den <= 0:
        return 0
    return num * scale // den


def _permille(num: int, den: int) -> int:
    return _ratio(num, den, 1000)


def _mean(total: int, count: int) -> int:
    return _ratio(total, count, 1)


def _nearest_rank(sorted_values: list[int], percent: int) -> int:
    """Nearest-rank percentile of an ascending sample; ``0`` when empty."""
    if not sorted_values:
        return 0
    rank = (len(sorted_values) * percent + 99) // 100  # ceil(N * P / 100), 1-based
    return sorted_values[max(rank, 1) - 1]


def _payload_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


# ------------------------------------------------------------------ retread


def _pair_key(step: Any) -> _Pair:
    """Canonical unordered pair of one executed step ``[a, b]``."""
    a, b = str(step[0]), str(step[1])
    return (a, b) if a <= b else (b, a)


class _StepTally:
    """novel / retread_slag / retread_recipe counters (internal)."""

    def __init__(self) -> None:
        self.novel = 0
        self.retread_slag = 0
        self.retread_recipe = 0

    def record(self, kind: str) -> None:
        if kind == "novel":
            self.novel += 1
        elif kind == "retread_slag":
            self.retread_slag += 1
        else:
            self.retread_recipe += 1

    @property
    def steps(self) -> int:
        return self.novel + self.retread_slag + self.retread_recipe

    @property
    def retread(self) -> int:
        return self.retread_slag + self.retread_recipe

    def as_dict(self) -> dict[str, int]:
        return {
            "steps": self.steps,
            "novel": self.novel,
            "retread_slag": self.retread_slag,
            "retread_recipe": self.retread_recipe,
            "retread": self.retread,
            "retread_permille": _permille(self.retread, self.steps),
        }


class _RetreadAgent:
    """Mutable per-agent retread fold state (internal)."""

    def __init__(self, agent_id: str, name: str, spawn_day: int) -> None:
        self.agent_id = agent_id
        self.name = name
        self.spawn_day = spawn_day
        self.alive = True
        self.journal: dict[_Pair, str] = {}  # pair -> product, first try wins
        self.tally = _StepTally()
        self.attempts = 0
        self.attempts_empty = 0
        self.attempts_zero_novel = 0
        self.pair_tries: Counter[_Pair] = Counter()  # every executed step
        self.slag_retries: Counter[_Pair] = Counter()  # retread_slag steps only
        self.by_life_day: dict[int, _StepTally] = {}

    def life_tally(self, day: int) -> _StepTally:
        return self.by_life_day.setdefault(day - self.spawn_day, _StepTally())

    def classify(self, pair: _Pair, product: str) -> str:
        """Classify against the journal, then record a novel pair."""
        known = self.journal.get(pair)
        if known is None:
            self.journal[pair] = product
            return "novel"
        return "retread_slag" if known == SLAG else "retread_recipe"

    def max_repeats(self) -> dict[str, Any]:
        if not self.pair_tries:
            return {"count": 0, "pair": []}
        best = max(self.pair_tries.values())
        pair = min(p for p, n in self.pair_tries.items() if n == best)
        return {"count": best, "pair": list(pair)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "spawn_day": self.spawn_day,
            **self.tally.as_dict(),
            "attempts": self.attempts,
            "attempts_empty": self.attempts_empty,
            "attempts_zero_novel": self.attempts_zero_novel,
            "max_repeats_of_one_pair": self.max_repeats(),
            "by_life_day": [
                {"life_day": life_day, **self.by_life_day[life_day].as_dict()}
                for life_day in sorted(self.by_life_day)
            ],
        }


def _executed_steps(payload: dict[str, Any]) -> list[tuple[Any, str]]:
    """``(step, product)`` for the executed prefix of a TASK_ATTEMPT."""
    steps = payload.get("steps")
    products = payload.get("step_products")
    if not isinstance(steps, list) or not isinstance(products, list):
        return []
    return [(step, str(product)) for step, product in zip(steps, products, strict=False)]


def _fold_attempt(agent: _RetreadAgent, ev: EventRecord, day_tally: _StepTally) -> None:
    """Classify one attempt's executed steps in order (see module docstring)."""
    executed = _executed_steps(ev.payload)
    agent.attempts += 1
    if not executed:
        agent.attempts_empty += 1
        return
    life_tally = agent.life_tally(ev.day)
    novel = 0
    for step, product in executed:
        pair = _pair_key(step)
        kind = agent.classify(pair, product)
        agent.pair_tries[pair] += 1
        if kind == "novel":
            novel += 1
        elif kind == "retread_slag":
            agent.slag_retries[pair] += 1
        for tally in (agent.tally, life_tally, day_tally):
            tally.record(kind)
    if novel == 0:
        agent.attempts_zero_novel += 1


def _known_slag_top(order: list[str], agents: dict[str, _RetreadAgent]) -> list[dict[str, Any]]:
    rows = [
        (-count, index, pair, agent_id)
        for index, agent_id in enumerate(order)
        for pair, count in agents[agent_id].slag_retries.items()
    ]
    rows.sort()
    return [
        {"agent_id": agent_id, "pair": list(pair), "count": -neg}
        for neg, _, pair, agent_id in rows[:KNOWN_SLAG_TOP_N]
    ]


def _population(order: list[str], agents: dict[str, _RetreadAgent]) -> dict[str, int]:
    total = _StepTally()
    attempts = attempts_empty = attempts_zero_novel = 0
    for agent_id in order:
        agent = agents[agent_id]
        total.novel += agent.tally.novel
        total.retread_slag += agent.tally.retread_slag
        total.retread_recipe += agent.tally.retread_recipe
        attempts += agent.attempts
        attempts_empty += agent.attempts_empty
        attempts_zero_novel += agent.attempts_zero_novel
    return {
        "agents": len(order),
        **total.as_dict(),
        "attempts": attempts,
        "attempts_empty": attempts_empty,
        "attempts_zero_novel": attempts_zero_novel,
    }


def fold_retread(events: list[EventRecord]) -> dict[str, Any]:
    """The retread fold; see the module docstring for every rule."""
    order: list[str] = []
    agents: dict[str, _RetreadAgent] = {}
    by_day: dict[int, _StepTally] = {}
    for ev in events:
        if ev.kind is EventKind.AGENT_SPAWNED:
            agent_id = str(ev.payload.get("agent_id", ""))
            order.append(agent_id)
            agent = _RetreadAgent(agent_id, str(ev.payload.get("name", agent_id)), ev.day)
            agents[agent_id] = agent
            agent.life_tally(ev.day)
        elif ev.kind is EventKind.DAY_STARTED:
            by_day.setdefault(ev.day, _StepTally())
            for agent in agents.values():
                if agent.alive and ev.day >= agent.spawn_day:
                    agent.life_tally(ev.day)
        elif ev.kind is EventKind.AGENT_DIED:
            agents[ev.actor].alive = False
        elif ev.kind is EventKind.TASK_ATTEMPT:
            _fold_attempt(agents[ev.actor], ev, by_day.setdefault(ev.day, _StepTally()))
    return {
        "agents": [agents[agent_id].as_dict() for agent_id in order],
        "population": _population(order, agents),
        "retread_by_day": [{"day": day, **by_day[day].as_dict()} for day in sorted(by_day)],
        "known_slag_retries_top": _known_slag_top(order, agents),
    }


# --------------------------------------------------------------- truncation


class _PurposeStats:
    """Per-purpose LLM_CALL counters (internal)."""

    def __init__(self) -> None:
        self.calls = 0
        self.truncated = 0
        self.kept_out: list[int] = []  # usage_out of non-truncated calls
        self.max_tokens_seen: set[int] = set()

    def record(self, usage_out: int, max_tokens: int, truncated: bool) -> None:
        self.calls += 1
        self.max_tokens_seen.add(max_tokens)
        if truncated:
            self.truncated += 1
        else:
            self.kept_out.append(usage_out)

    def as_dict(self) -> dict[str, Any]:
        sample = sorted(self.kept_out)
        return {
            "calls": self.calls,
            "truncated": self.truncated,
            "truncated_permille": _permille(self.truncated, self.calls),
            "usage_out_p50": _nearest_rank(sample, 50),
            "usage_out_p90": _nearest_rank(sample, 90),
            "usage_out_max": sample[-1] if sample else 0,
            "max_tokens_seen": sorted(self.max_tokens_seen),
        }


class _TruncAgent:
    """Mutable per-agent truncation fold state (internal)."""

    def __init__(self, agent_id: str, name: str, spawn_day: int) -> None:
        self.agent_id = agent_id
        self.name = name
        self.spawn_day = spawn_day
        self.alive = True
        self.death_day = NO_DEATH
        self.calls = 0
        self.usage_in = 0
        self.tick_calls = 0
        self.usage_in_tick = 0
        self.qi_thinking = 0
        self.qi_spent = 0
        self.active_days: set[int] = set()

    def record_call(self, purpose: str, usage_in: int, cost: int, day: int) -> None:
        self.calls += 1
        self.usage_in += usage_in
        self.qi_thinking += cost
        self.active_days.add(day)
        if purpose == "tick":
            self.tick_calls += 1
            self.usage_in_tick += usage_in

    def as_dict(self) -> dict[str, Any]:
        days_active = len(self.active_days)
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "alive": self.alive,
            "spawn_day": self.spawn_day,
            "death_day": self.death_day,
            "calls": self.calls,
            "usage_in": self.usage_in,
            "mean_usage_in": _mean(self.usage_in, self.calls),
            "tick_calls": self.tick_calls,
            "usage_in_tick": self.usage_in_tick,
            "mean_usage_in_tick": _mean(self.usage_in_tick, self.tick_calls),
            "qi_thinking": self.qi_thinking,
            "qi_spent": self.qi_spent,
            "days_active": days_active,
            "qi_per_day": _mean(self.qi_spent, days_active),
        }


class _SlotStats:
    """Slot- and qi-level truncation counters (internal)."""

    def __init__(self, group_sizes: Counter[_SlotKey]) -> None:
        self.group_sizes = group_sizes
        self.seen: Counter[_SlotKey] = Counter()  # calls folded so far per group
        self.first_truncated: dict[_SlotKey, bool] = {}
        self.actions = 0
        self.actions_no_call = 0
        self.actions_single_call = 0
        self.actions_with_retry = 0
        self.actions_with_retry_after_truncation = 0
        self.malformed_forfeits = 0
        self.qi_thinking_total = 0
        self.truncated_then_retried_calls = 0
        self.qi_on_truncated_first_calls = 0
        self.qi_on_retry_calls = 0

    def record_tick_call(self, key: _SlotKey, truncated: bool, cost: int) -> None:
        self.seen[key] += 1
        ordinal = self.seen[key]  # 1-based position inside the slot's group
        if ordinal == 1:
            self.first_truncated[key] = truncated
        else:
            self.qi_on_retry_calls += cost
        if truncated and ordinal < self.group_sizes[key]:
            self.truncated_then_retried_calls += 1
            self.qi_on_truncated_first_calls += cost

    def record_action(self, key: _SlotKey, payload: dict[str, Any]) -> None:
        self.actions += 1
        size = self.group_sizes.get(key, 0)
        if size == 0:
            self.actions_no_call += 1
        elif size == 1:
            self.actions_single_call += 1
        else:
            self.actions_with_retry += 1
            if self.first_truncated.get(key, False):
                self.actions_with_retry_after_truncation += 1
        if payload.get("degraded") and payload.get("reason") == "malformed":
            self.malformed_forfeits += 1

    def slots_dict(self) -> dict[str, int]:
        return {
            "actions": self.actions,
            "actions_no_call": self.actions_no_call,
            "actions_single_call": self.actions_single_call,
            "actions_with_retry": self.actions_with_retry,
            "retry_permille": _permille(self.actions_with_retry, self.actions),
            "actions_with_retry_after_truncation": self.actions_with_retry_after_truncation,
            "malformed_forfeits": self.malformed_forfeits,
        }

    def qi_dict(self) -> dict[str, int]:
        return {
            "qi_thinking_total": self.qi_thinking_total,
            "truncated_then_retried_calls": self.truncated_then_retried_calls,
            "qi_on_truncated_first_calls": self.qi_on_truncated_first_calls,
            "share_permille": _permille(self.qi_on_truncated_first_calls, self.qi_thinking_total),
            "qi_on_retry_calls": self.qi_on_retry_calls,
        }


def _tick_group_sizes(events: list[EventRecord]) -> Counter[_SlotKey]:
    """Tick-purpose LLM_CALLs per (actor, day, tick) — the pre-pass."""
    sizes: Counter[_SlotKey] = Counter()
    for ev in events:
        if ev.kind is EventKind.LLM_CALL and ev.payload.get("purpose") == "tick":
            sizes[(ev.actor, ev.day, ev.tick)] += 1
    return sizes


def fold_truncation(events: list[EventRecord]) -> dict[str, Any]:
    """The truncation fold; see the module docstring for every rule."""
    order: list[str] = []
    agents: dict[str, _TruncAgent] = {}
    purposes: dict[str, _PurposeStats] = {"tick": _PurposeStats(), "reflection": _PurposeStats()}
    slots = _SlotStats(_tick_group_sizes(events))
    for ev in events:
        agent = agents.get(ev.actor)
        if agent is not None and ev.qi_delta < 0:
            agent.qi_spent += -ev.qi_delta
        if ev.kind is EventKind.AGENT_SPAWNED:
            agent_id = str(ev.payload.get("agent_id", ""))
            order.append(agent_id)
            agents[agent_id] = _TruncAgent(agent_id, str(ev.payload.get("name", agent_id)), ev.day)
        elif ev.kind is EventKind.AGENT_DIED:
            agents[ev.actor].alive = False
            agents[ev.actor].death_day = ev.day
        elif ev.kind is EventKind.LLM_CALL:
            purpose = str(ev.payload.get("purpose", ""))
            usage_out = _payload_int(ev.payload, "usage_out")
            max_tokens = _payload_int(ev.payload, "max_tokens")
            truncated = usage_out >= max_tokens
            cost = -ev.qi_delta if ev.qi_delta < 0 else 0
            usage_in = _payload_int(ev.payload, "usage_in")
            purposes.setdefault(purpose, _PurposeStats()).record(usage_out, max_tokens, truncated)
            slots.qi_thinking_total += cost
            agents[ev.actor].record_call(purpose, usage_in, cost, ev.day)
            if purpose == "tick":
                slots.record_tick_call((ev.actor, ev.day, ev.tick), truncated, cost)
        elif ev.kind is EventKind.ACTION:
            agents[ev.actor].active_days.add(ev.day)
            slots.record_action((ev.actor, ev.day, ev.tick), ev.payload)
    return {
        "by_purpose": {purpose: purposes[purpose].as_dict() for purpose in sorted(purposes)},
        "slots": slots.slots_dict(),
        "qi": slots.qi_dict(),
        "agents": [agents[agent_id].as_dict() for agent_id in order],
    }


# -------------------------------------------------------------------- write


def _run_id(events: list[EventRecord]) -> str:
    for ev in events:
        if ev.kind is EventKind.RUN_STARTED:
            return str(ev.payload.get("run_id", ""))
    return ""


def write_retread(run_dir: str | Path, out_path: str | Path | None = None) -> Path:
    """Write ``retread.json`` for *run_dir* (default ``run_dir/retread.json``;
    ``out_path`` redirects the write, e.g. away from an archived run) and
    return its path. Pure fold of the event log — deterministic and
    byte-identical across re-writes (see module docstring)."""
    rd = Path(run_dir)
    with EventStore(rd / "events.sqlite3", readonly=True) as store:
        events = list(store.scan())
    data = {
        "run_id": _run_id(events),
        "retread": fold_retread(events),
        "truncation": fold_truncation(events),
    }
    path = Path(out_path) if out_path is not None else rd / "retread.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
