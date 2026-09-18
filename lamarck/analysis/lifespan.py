"""Lifespan schedules — the $0 simulator that sets Phase 2's canary bars.

Background (Phase-2 plan, corrected calibration): every agent's per-day qi
spend is nearly uniform, qi is never replenished, death is checked at dusk
when ``qi <= 0``, and Phase 2 gives each dead agent a successor spawned at
the NEXT dawn with full ``qi_max``. Replaying the founders' measured spend
against candidate ``qi_max`` values shows every founder dying inside a
two-day window — a population cliff. This module lets the plan reproduce
that, evaluate stagger schemes, and compute canary bars (deaths per arm,
generations reached, teacher–heir overlap) for a given arm length.

Everything here is pure: ``spend_table`` is a fold over the event log;
``simulate`` and ``canary_check`` are functions of plain ints. Outputs hold
ints, strings, bools, lists, dicts and null only — never floats (means are
floor-divided, medians are the LOWER median for even counts). Ordering is
deterministic everywhere (lineages in founder spawn order, days and
generations ascending, JSON keys sorted), so two writes over the same log
are byte-identical. The module never mutates a run directory except to
write its own ``lifespan.json``.

Rules
-----

``spend_table(events) -> {agent_id: [qi_day0, qi_day1, ...]}``

- agents in AGENT_SPAWNED order; days ``0..max_day`` where ``max_day`` is
  the largest ``day`` of any DAY_STARTED event (no DAY_STARTED => every
  series is empty);
- ``spend[agent][d]`` = ``-sum(qi_delta)`` over the agent's ACTION and
  LLM_CALL events with ``qi_delta < 0`` on day ``d``; ``0`` on days with
  no spend (including days after the agent's death in the measured run).

``simulate(spend, qi_max, days, *, stagger, replacement, stagger_seed)``

- one lineage per founder, in spawn order; the founder's measured series is
  the spend profile of every generation of that lineage;
- founder ``i`` is generation 1, born on day 0 with
  ``start_qi = qi_max * f_i // 1000`` where ``f_i`` (permille, 1..1000)
  comes from ``stagger``: ``None`` => 1000 for everyone; an explicit list
  (one permille per founder); or ``("uniform", lo, hi)`` =>
  ``random.Random(stagger_seed).randint(lo, hi)`` drawn once per founder in
  spawn order (``stagger_seed`` is required). This is ANALYSIS RNG — a
  plain seeded ``random.Random``, not an engine RNG stream; it never
  touches a run;
- on each day ``d`` in ``0..days-1`` every living member spends
  ``series[age % len(series)]`` with ``age = d - birth_day`` (age-aligned
  recycling: a successor's day-0 spend is its founder's day-0 spend);
- ``qi <= 0`` after the day's spend => the member dies at that day's dusk:
  ``death_day = d`` (it was alive on ``d``);
- with ``replacement`` the lineage's successor is born on day ``d + 1``
  with ``start_qi = qi_max`` and ``generation + 1`` — only if ``d + 1 <
  days``; without replacement a lineage ends at its founder;
- alive on day ``d`` := ``birth_day <= d`` and (``death_day`` is null or
  ``d <= death_day``).

Output of ``simulate`` (all ints / null / lists / dicts):

- ``lineages``: in spawn order — ``lineage`` (agent_id), ``series_len``,
  ``stagger_permille`` and ``members``: ``[{generation, birth_day,
  death_day (null while alive), start_qi, qi_remaining}]``;
- ``population.deaths_by_day``: one count per simulated day;
- ``population.generations``: per generation ``g`` (1..max reached):
  ``born``, ``deaths``, ``first_death_day`` / ``last_death_day`` (null when
  nobody of that generation died);
- ``population.generations_reached``: per generation ``g``:
  ``first_day_any`` (the first day any lineage has a living member of
  generation ``>= g``) and ``first_day_half`` (the first day on which at
  least ``half_threshold = ceil(n_lineages / 2)`` lineages have a living
  member of generation ``>= g``; null if never);
- ``population.overlap.cross_lineage``: per consecutive pair ``(g, g+1)``:
  ``days_both_alive`` (days on which some gen-``g`` member and some
  gen-``g+1`` member — of ANY lineages — are both alive) and
  ``heir_agent_days_with_teacher`` (member-days of gen-``g+1`` members on
  days when some gen-``g`` member is alive);
- ``population.overlap.same_lineage``: per lineage the days on which two
  consecutive generations of THAT lineage are both alive — always 0 under
  next-dawn replacement (asserted and reported so the number is explicit);
- ``population.spread``: ``last_death_day - first_death_day`` of
  generation 1 (null when no founder died);
- ``population.survivors``: members still living after the final dusk
  (``death_day`` null).

``canary_check(sim, arm_days, *, min_deaths, min_generation)`` — pure
bookkeeping over a ``simulate`` result for an arm of ``arm_days`` days
(``1 <= arm_days <= sim["days"]``): ``deaths_within_arm`` (deaths at the
dusks of days ``0..arm_days-1``), ``successors_born`` (generation >= 2,
``birth_day <= arm_days - 1``), ``successors_acting_days`` (their
member-days inside the arm — a successor born on the arm's last day counts
as acting 1 day), ``generation_reached_by_arm_end`` (max ``g`` with
``first_day_any <= arm_days - 1``), ``generation_reached_by_half_by_arm_end``
(same with ``first_day_half``), ``passes`` iff ``deaths_within_arm >=
min_deaths`` and ``generation_reached_by_arm_end >= min_generation``, and
``reasons`` (one string per failed bar, empty when it passes).

``write_lifespan(run_dir, qi_max_list, days, *, stagger, stagger_seed,
arm_days, out_path) -> Path`` writes ``lifespan.json`` = ``{run_id, days,
spend_summary, scenarios}`` to ``run_dir/lifespan.json`` unless
``out_path``. It reads ``run_dir/events.sqlite3`` only, and refuses
(``FileNotFoundError``) when that file is missing: opening a store on a
missing path would CREATE an empty log, a mutation this module never
makes. ``spend_summary`` holds ``agents`` (spawn order),
``measured_days``, per-agent ``total`` / ``mean`` (floor) / ``median``
(lower) dicts keyed by agent_id, and ``all_days_{mean,median,min,max}``
over every entry of the spend table. ``scenarios`` is one ``simulate``
result per ``qi_max`` (in the given order), each carrying a ``canary``
entry when ``arm_days`` is given.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import EventKind, EventRecord
from lamarck.eventstore import EventStore

__all__ = ["canary_check", "simulate", "spend_table", "write_lifespan"]

PERMILLE_FULL = 1000
UNIFORM = "uniform"

StaggerSpec = list[int] | tuple[str, int, int] | None
"""``None`` (everyone full), an explicit permille list (one per founder),
or ``("uniform", lo_permille, hi_permille)`` drawn with ``stagger_seed``."""

SPEND_KINDS = frozenset({EventKind.ACTION, EventKind.LLM_CALL})


# ------------------------------------------------------------- spend table


def spend_table(events: Sequence[EventRecord]) -> dict[str, list[int]]:
    """Per-agent per-day qi spend (see module docstring, "spend_table")."""
    spawn_order: list[str] = []
    spent: dict[str, dict[int, int]] = {}
    max_day = -1
    for ev in events:
        if ev.kind is EventKind.AGENT_SPAWNED:
            agent_id = str(ev.payload.get("agent_id", ""))
            spawn_order.append(agent_id)
            spent[agent_id] = {}
        elif ev.kind is EventKind.DAY_STARTED:
            max_day = max(max_day, ev.day)
        elif ev.kind in SPEND_KINDS and ev.qi_delta < 0:
            LMK_ASSERT(ev.actor in spent, "spend by an unspawned actor", seq=ev.seq, actor=ev.actor)
            LMK_ASSERT(ev.day <= max_day, "spend before its DAY_STARTED", seq=ev.seq, day=ev.day)
            by_day = spent[ev.actor]
            by_day[ev.day] = by_day.get(ev.day, 0) - ev.qi_delta
    return {
        agent_id: [spent[agent_id].get(day, 0) for day in range(max_day + 1)]
        for agent_id in spawn_order
    }


# -------------------------------------------------------------- simulation


class _Member:
    """One agent of a lineage (internal, mutable during the simulation)."""

    def __init__(self, generation: int, birth_day: int, start_qi: int) -> None:
        self.generation = generation
        self.birth_day = birth_day
        self.death_day: int | None = None
        self.start_qi = start_qi
        self.qi = start_qi

    def alive_on(self, day: int) -> bool:
        return self.birth_day <= day and (self.death_day is None or day <= self.death_day)

    def as_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "birth_day": self.birth_day,
            "death_day": self.death_day,
            "start_qi": self.start_qi,
            "qi_remaining": self.qi,
        }


def _validate_spend(spend: dict[str, list[int]]) -> None:
    if not spend:
        raise ValueError("spend table is empty")
    for agent_id, series in spend.items():
        if not series:
            raise ValueError(f"spend series for {agent_id!r} is empty")
        if any(s < 0 for s in series):
            raise ValueError(f"spend series for {agent_id!r} holds a negative entry")


def _resolve_stagger(stagger: StaggerSpec, n: int, stagger_seed: int | None) -> list[int]:
    """The per-founder permille list a stagger spec denotes (see docstring)."""
    if stagger is None:
        return [PERMILLE_FULL] * n
    if isinstance(stagger, list):
        if len(stagger) != n:
            raise ValueError(f"stagger list has {len(stagger)} entries for {n} founders")
        permille = list(stagger)
    else:
        kind, lo, hi = stagger
        if kind != UNIFORM:
            raise ValueError(f"unknown stagger scheme {kind!r}")
        if not 1 <= lo <= hi <= PERMILLE_FULL:
            raise ValueError(f"uniform stagger needs 1 <= lo <= hi <= 1000, got {lo}..{hi}")
        if stagger_seed is None:
            raise ValueError("uniform stagger requires stagger_seed")
        rng = random.Random(stagger_seed)  # analysis RNG, not an engine stream
        permille = [rng.randint(lo, hi) for _ in range(n)]
    for f in permille:
        if not 1 <= f <= PERMILLE_FULL:
            raise ValueError(f"stagger permille {f} outside 1..1000")
    return permille


def _stagger_as_json(stagger: StaggerSpec) -> list[str | int] | None:
    """The stagger spec as it appears in JSON (a tuple becomes a list)."""
    if stagger is None:
        return None
    spec: list[str | int] = []
    spec.extend(stagger)
    return spec


def _run_lineages(
    series_by_lineage: list[list[int]],
    qi_max: int,
    days: int,
    permille: list[int],
    replacement: bool,
) -> list[list[_Member]]:
    """The day loop: spend, dusk death check, next-dawn replacement."""
    lineages = [[_Member(1, 0, qi_max * f // PERMILLE_FULL)] for f in permille]
    for day in range(days):
        for series, members in zip(series_by_lineage, lineages, strict=True):
            current = members[-1]
            if not current.alive_on(day):
                continue
            current.qi -= series[(day - current.birth_day) % len(series)]
            if current.qi <= 0:
                current.death_day = day
                if replacement and day + 1 < days:
                    members.append(_Member(current.generation + 1, day + 1, qi_max))
    return lineages


def _living_by_day(lineages: list[list[_Member]], days: int) -> list[list[_Member | None]]:
    """``[lineage][day]`` -> the living member that day (at most one)."""
    table: list[list[_Member | None]] = []
    for members in lineages:
        row: list[_Member | None] = [None] * days
        for m in members:
            last = days - 1 if m.death_day is None else m.death_day
            for day in range(m.birth_day, last + 1):
                LMK_ASSERT(row[day] is None, "two living members in one lineage", day=day)
                row[day] = m
        table.append(row)
    return table


def _alive_on_day(living: list[list[_Member | None]], day: int) -> list[_Member]:
    """The living members of every lineage on *day*, in lineage order."""
    return [m for m in (row[day] for row in living) if m is not None]


def _generation_rows(members: list[_Member], max_gen: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for g in range(1, max_gen + 1):
        deaths = sorted(
            m.death_day for m in members if m.generation == g and m.death_day is not None
        )
        rows.append(
            {
                "generation": g,
                "born": sum(1 for m in members if m.generation == g),
                "deaths": len(deaths),
                "first_death_day": deaths[0] if deaths else None,
                "last_death_day": deaths[-1] if deaths else None,
            }
        )
    return rows


def _generations_reached(
    living: list[list[_Member | None]], max_gen: int, days: int, half: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for g in range(1, max_gen + 1):
        first_any: int | None = None
        first_half: int | None = None
        for day in range(days):
            deep = sum(1 for m in _alive_on_day(living, day) if m.generation >= g)
            if deep >= 1 and first_any is None:
                first_any = day
            if deep >= half and first_half is None:
                first_half = day
        rows.append({"generation": g, "first_day_any": first_any, "first_day_half": first_half})
    return rows


def _cross_lineage_overlap(
    living: list[list[_Member | None]], max_gen: int, days: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for g in range(1, max_gen):
        days_both = 0
        heir_days = 0
        for day in range(days):
            alive = _alive_on_day(living, day)
            heirs = sum(1 for m in alive if m.generation == g + 1)
            if heirs and any(m.generation == g for m in alive):
                days_both += 1
                heir_days += heirs
        rows.append(
            {
                "generation": g,
                "next_generation": g + 1,
                "days_both_alive": days_both,
                "heir_agent_days_with_teacher": heir_days,
            }
        )
    return rows


def _same_lineage_overlap(
    lineage_ids: list[str], lineages: list[list[_Member]], days: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lineage_id, members in zip(lineage_ids, lineages, strict=True):
        both = 0
        for teacher, heir in zip(members, members[1:], strict=False):
            both += sum(1 for day in range(days) if teacher.alive_on(day) and heir.alive_on(day))
        LMK_ASSERT(both == 0, "teacher and heir overlapped under next-dawn replacement", days=both)
        rows.append({"lineage": lineage_id, "teacher_heir_days": both})
    return rows


def _population(lineage_ids: list[str], lineages: list[list[_Member]], days: int) -> dict[str, Any]:
    members = [m for ms in lineages for m in ms]
    max_gen = max(m.generation for m in members)
    half = -(-len(lineages) // 2)
    living = _living_by_day(lineages, days)
    deaths_by_day = [0] * days
    for m in members:
        if m.death_day is not None:
            deaths_by_day[m.death_day] += 1
    generations = _generation_rows(members, max_gen)
    founders = generations[0]
    spread = (
        None
        if founders["first_death_day"] is None
        else founders["last_death_day"] - founders["first_death_day"]
    )
    same_lineage = _same_lineage_overlap(lineage_ids, lineages, days)
    return {
        "deaths_by_day": deaths_by_day,
        "generations": generations,
        "half_threshold": half,
        "generations_reached": _generations_reached(living, max_gen, days, half),
        "overlap": {
            "cross_lineage": _cross_lineage_overlap(living, max_gen, days),
            "same_lineage": same_lineage,
            "same_lineage_total": sum(row["teacher_heir_days"] for row in same_lineage),
        },
        "spread": spread,
        "survivors": sum(1 for m in members if m.death_day is None),
    }


def simulate(
    spend: dict[str, list[int]],
    qi_max: int,
    days: int,
    *,
    stagger: StaggerSpec = None,
    replacement: bool = True,
    stagger_seed: int | None = None,
) -> dict[str, Any]:
    """Deterministic lifespan simulation (see module docstring, "simulate")."""
    _validate_spend(spend)
    if qi_max < 1:
        raise ValueError(f"qi_max must be >= 1, got {qi_max}")
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")
    lineage_ids = list(spend)
    permille = _resolve_stagger(stagger, len(lineage_ids), stagger_seed)
    series_by_lineage = [list(spend[aid]) for aid in lineage_ids]
    lineages = _run_lineages(series_by_lineage, qi_max, days, permille, replacement)
    return {
        "qi_max": qi_max,
        "days": days,
        "replacement": replacement,
        "stagger": _stagger_as_json(stagger),
        "stagger_seed": stagger_seed,
        "stagger_permille": permille,
        "lineages": [
            {
                "lineage": aid,
                "series_len": len(series),
                "stagger_permille": f,
                "members": [m.as_dict() for m in members],
            }
            for aid, series, f, members in zip(
                lineage_ids, series_by_lineage, permille, lineages, strict=True
            )
        ],
        "population": _population(lineage_ids, lineages, days),
    }


# ------------------------------------------------------------ canary bars


def canary_check(
    sim: dict[str, Any],
    arm_days: int,
    *,
    min_deaths: int = 1,
    min_generation: int = 2,
    deaths_by_day: tuple[int, int] | None = None,
    min_spread: int | None = None,
    min_successor_days: int | None = None,
    min_overlap_days: int | None = None,
    generation_half_by_day: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Canary bars for an arm of ``arm_days`` days over a ``simulate`` result
    (see module docstring, "canary_check"); no re-simulation.

    Optional schedule bars (each adds a reason when unmet), all in the
    simulator's 0-indexed day units: ``deaths_by_day=(day, n)`` requires at
    least ``n`` deaths on days ``0..day``; ``min_spread`` bounds the
    generation-1 first-to-last death spread from below;
    ``min_successor_days`` bounds ``successors_acting_days``;
    ``min_overlap_days`` bounds ``heir_agent_days_with_teacher`` for the
    generation 1 -> 2 pair; ``generation_half_by_day=(g, day)`` requires half
    the lineages to have reached generation ``g`` by ``day``."""
    days = int(sim["days"])
    if not 1 <= arm_days <= days:
        raise ValueError(f"arm_days must be within 1..{days}, got {arm_days}")
    last = arm_days - 1
    population = sim["population"]
    deaths_within_arm = sum(int(d) for d in population["deaths_by_day"][:arm_days])
    successors_born = 0
    successors_acting_days = 0
    for lineage in sim["lineages"]:
        for member in lineage["members"]:
            if member["generation"] == 1 or member["birth_day"] > last:
                continue
            successors_born += 1
            last_alive = days - 1 if member["death_day"] is None else int(member["death_day"])
            successors_acting_days += min(last_alive, last) - int(member["birth_day"]) + 1
    reached = population["generations_reached"]
    by_any = max(int(r["generation"]) for r in reached if r["first_day_any"] <= last)
    by_half = max(
        int(r["generation"])
        for r in reached
        if r["first_day_half"] is not None and r["first_day_half"] <= last
    )
    spread_raw = population["spread"]
    spread = None if spread_raw is None else int(spread_raw)
    overlap_1_2 = 0
    for row in population["overlap"]["cross_lineage"]:
        if int(row["generation"]) == 1:
            overlap_1_2 = int(row["heir_agent_days_with_teacher"])
    half_days = {int(r["generation"]): r["first_day_half"] for r in reached}
    reasons: list[str] = []
    if deaths_within_arm < min_deaths:
        reasons.append(f"deaths_within_arm {deaths_within_arm} < min_deaths {min_deaths}")
    if by_any < min_generation:
        reasons.append(f"generation_reached_by_arm_end {by_any} < min_generation {min_generation}")
    deaths_by_limit: int | None = None
    if deaths_by_day is not None:
        limit_day, needed = deaths_by_day
        deaths_by_limit = sum(
            int(d) for d in population["deaths_by_day"][: min(limit_day, last) + 1]
        )
        if deaths_by_limit < needed:
            reasons.append(f"deaths_by_day {limit_day}: {deaths_by_limit} < {needed}")
    if min_spread is not None and (spread is None or spread < min_spread):
        reasons.append(f"spread {spread} < min_spread {min_spread}")
    if min_successor_days is not None and successors_acting_days < min_successor_days:
        reasons.append(
            f"successors_acting_days {successors_acting_days} < min_successor_days "
            f"{min_successor_days}"
        )
    if min_overlap_days is not None and overlap_1_2 < min_overlap_days:
        reasons.append(f"overlap_heir_days_1_2 {overlap_1_2} < min_overlap_days {min_overlap_days}")
    if generation_half_by_day is not None:
        g, by_day = generation_half_by_day
        reached_day = half_days.get(g)
        if reached_day is None or int(reached_day) > min(by_day, last):
            reasons.append(f"generation {g} by half not reached by day {by_day} ({reached_day})")
    return {
        "arm_days": arm_days,
        "min_deaths": min_deaths,
        "min_generation": min_generation,
        "deaths_within_arm": deaths_within_arm,
        "deaths_by_day_limit": deaths_by_limit,
        "spread": spread,
        "successors_born": successors_born,
        "successors_acting_days": successors_acting_days,
        "overlap_heir_days_1_2": overlap_1_2,
        "generation_reached_by_arm_end": by_any,
        "generation_reached_by_half_by_arm_end": by_half,
        "first_day_half_by_generation": {str(g): d for g, d in sorted(half_days.items())},
        "passes": not reasons,
        "reasons": reasons,
    }


# --------------------------------------------------------------- the file


def _lower_median(values: Sequence[int]) -> int:
    """Median as an int: the middle value, or the LOWER middle for even
    counts (never an average, so never a float); 0 for no values."""
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def _spend_summary(spend: dict[str, list[int]]) -> dict[str, Any]:
    everything = [s for series in spend.values() for s in series]
    return {
        "agents": list(spend),
        "measured_days": max((len(series) for series in spend.values()), default=0),
        "per_agent_total": {aid: sum(series) for aid, series in spend.items()},
        "per_agent_mean": {
            aid: sum(series) // len(series) if series else 0 for aid, series in spend.items()
        },
        "per_agent_median": {aid: _lower_median(series) for aid, series in spend.items()},
        "all_days_mean": sum(everything) // len(everything) if everything else 0,
        "all_days_median": _lower_median(everything),
        "all_days_min": min(everything, default=0),
        "all_days_max": max(everything, default=0),
    }


def _run_id(events: Sequence[EventRecord]) -> str:
    for ev in events:
        if ev.kind is EventKind.RUN_STARTED:
            return str(ev.payload.get("run_id", ""))
    return ""


def _build_lifespan(
    events: Sequence[EventRecord],
    qi_max_list: Sequence[int],
    days: int,
    *,
    stagger: StaggerSpec,
    stagger_seed: int | None,
    arm_days: int | None,
) -> dict[str, Any]:
    spend = spend_table(events)
    scenarios: list[dict[str, Any]] = []
    for qi_max in qi_max_list:
        scenario = simulate(spend, qi_max, days, stagger=stagger, stagger_seed=stagger_seed)
        if arm_days is not None:
            scenario["canary"] = canary_check(scenario, arm_days)
        scenarios.append(scenario)
    return {
        "run_id": _run_id(events),
        "days": days,
        "spend_summary": _spend_summary(spend),
        "scenarios": scenarios,
    }


def write_lifespan(
    run_dir: str | Path,
    qi_max_list: Sequence[int],
    days: int,
    *,
    stagger: StaggerSpec = None,
    stagger_seed: int | None = None,
    arm_days: int | None = None,
    out_path: str | Path | None = None,
) -> Path:
    """Write ``lifespan.json`` for *run_dir* (to *out_path* when given) and
    return its path. Reads ``events.sqlite3`` only; byte-identical across
    re-writes (see module docstring)."""
    rd = Path(run_dir)
    log = rd / "events.sqlite3"
    if not log.is_file():  # EventStore would create one — never touch a run dir
        raise FileNotFoundError(f"no event log at {log}")
    with EventStore(log, readonly=True) as store:
        events = list(store.scan())
    data = _build_lifespan(
        events, qi_max_list, days, stagger=stagger, stagger_seed=stagger_seed, arm_days=arm_days
    )
    path = rd / "lifespan.json" if out_path is None else Path(out_path)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
