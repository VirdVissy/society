"""Phase-0 sim runner and model-free replay verifier.

``run_sim`` drives the stub world. It owns event EMISSION only — the store
owns commitment (append returns the committed truth), the ledgers own
accounting (every committed record is fed back through ``apply``), the
scheduler owns slot order, and the policy owns choice. ``replay`` is the
other half of the reproducibility contract: it re-verifies a finished run
directory from its artifacts alone, with zero model of the sim — no
Scheduler, no StubPolicy, no RNG use on its code path. A run is
*reproducible* iff ``replay(run_dir).ok``.

========================================================================
RUN DIRECTORY LAYOUT
========================================================================
    events.sqlite3   the hash-chained event log (ground truth)
    config.toml      the run's world config. Byte-verbatim copy of the
                     source file when ``config_path`` is given; otherwise a
                     synthesized serialization of ``cfg``, asserted to
                     round-trip (reloading yields an identical config_sha).
    report.json      convenience summary (strings and ints only; wall time
                     as integer milliseconds). NEVER hashed and never read
                     by replay — regenerating it cannot change a
                     fingerprint.

``run_id = sha256_hex(utf8(config_sha(cfg) + ":" + cfg.world.master_seed))[:12]``
(the master seed as its verbatim config hex string).

========================================================================
EVENT LAYOUT (pinned; the golden test freezes its chain head)
========================================================================
1. RUN_STARTED (day 0, tick 0, world):
   ``{run_id, config_sha, master_seed, schema_version, engine_version}``.
2. AGENT_SPAWNED x founders (day 0, tick 0, world), spawn order a1..aN:
   ``{agent_id, qi_max, starting_stones}``; zero deltas.
3. Per day d (0-based), inside one ``store.batch()``:
   - DAY_STARTED (d, 0, world) ``{day}`` — resets the daily allowance.
   - The day's slots from ``Scheduler.iter_day(d, alive_in_spawn_order)``
     (the scheduler stream is consumed once, at call time). At each phase
     TRANSITION: PHASE_STARTED (d, slot tick, world) ``{day, phase}`` —
     dawn, first ACTION slot, dusk, night; 4/day (the action marker is
     absent when no agent is alive, which a terminating run never reaches).
   - ACTION slot for agent X at tick t: build
     ``StubView(day, round, tick, qi, stones, allowance_left)`` from the
     live ledgers, ask the policy, then check affordability IN THIS ORDER:
       (a) allowance: ``action_costs[atype]`` must fit the remaining daily
           allowance;
       (b) stones (EXPERIMENT only): ``stones >= materials[tier-1]``.
     Affordable: ACTION ``{type, **args}``, ``qi_delta = -cost``,
     ``stones_delta = -materials[tier-1]`` for EXPERIMENT else 0.
     Unaffordable: the slot DEGRADES —
     ACTION ``{type: "rest", degraded: true, wanted: {type, **args}}`` with
     ``qi_delta = -cost(REST)``; if even REST exceeds the remaining
     allowance, ``qi_delta = 0`` and the payload adds ``free: true``.
     After a NON-degraded EXPERIMENT (and only then), one draw from stream
     "stub-universe": ``randrange(1000) < stub_success_permille[tier-1]``
     appends LEDGER_ADJUST (same d, t, actor X)
     ``{reason: "bounty", tier}`` with ``stones_delta = +bounties[tier-1]``.
     Degraded slots never touch the stub-universe stream.
   - DUSK slot: after its PHASE_STARTED, AGENT_DIED (d, dusk tick, actor)
     ``{cause: "qi_exhausted"}`` in spawn order for alive agents with
     qi <= 0 (zero deltas); then, when ``difftest_interval > 0`` and
     ``d % difftest_interval == 0``, ``assert_difftest`` over a full scan.
   - NIGHT slot: phase marker only.
4. Termination: after the configured days, or early after the first day
   that ends with no agent alive (that day still completes dusk and night).
5. RUN_FINISHED (day = last completed day, tick = that day's night tick,
   world): ``{days_elapsed, final_state_sha}`` where ``final_state_sha`` is
   the sha256 of the canonical ``LedgerBalances`` dump. Appended outside
   any batch; then ``verify_chain()``; then one unconditional difftest.

Every committed record — including markers — is applied to the live
ledgers; marker kinds are accounting no-ops there by contract.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lamarck import __version__
from lamarck.agents.stub import StubPolicy
from lamarck.asserts import LMK_ASSERT, LamarckAssertionError
from lamarck.contracts import (
    GENESIS_HASH,
    SCHEMA_VERSION,
    WORLD_ACTOR,
    ActionType,
    DayPhase,
    EventDraft,
    EventKind,
    EventRecord,
    StubView,
    WorldConfig,
)
from lamarck.engine import (
    Ledgers,
    RngStreams,
    Scheduler,
    assert_difftest,
    config_sha,
    fold_balances,
    load_world_config,
)
from lamarck.eventstore import EventStore, canonical_bytes, sha256_hex

__all__ = ["ReplayResult", "RunSummary", "compute_run_id", "replay", "run_sim"]

STUB_UNIVERSE_STREAM = "stub-universe"

_CommitFn = Callable[[EventDraft], EventRecord]


class RunSummary(BaseModel):
    """What ``run_sim`` returns (and what report.json mirrors, minus
    ``discoveries``/``degraded``/``out_dir``). Ints and strings only."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    days_elapsed: int
    events: int
    head_seq: int
    head_hash: str
    final_state_sha: str
    alive_count: int
    qi_total: int
    stones_total: int
    discoveries: int  # committed bounty LEDGER_ADJUST events
    degraded: int  # ACTION events that degraded to rest (incl. free rests)
    wall_ms: int
    out_dir: str


class ReplayResult(BaseModel):
    """Verdict of a model-free replay. ``ok`` iff ``mismatches`` is empty."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    run_dir: str
    events: int
    head_seq: int
    head_hash: str
    days_elapsed: int | None
    recorded_state_sha: str | None
    refolded_state_sha: str | None
    mismatches: tuple[str, ...]


def compute_run_id(cfg: WorldConfig) -> str:
    """First 12 hex chars of sha256 over ``config_sha(cfg) + ":" + master_seed``.

    The master seed contributes as its verbatim config string (it is also
    inside config_sha; the concatenation keeps the id readable to derive).
    Deterministic in the config alone — same config, same run id.
    """
    return sha256_hex((config_sha(cfg) + ":" + cfg.world.master_seed).encode("utf-8"))[:12]


def _toml_string(value: str) -> str:
    """Encode a TOML basic string. JSON string escaping is a strict subset
    of TOML basic-string escaping, so ``json.dumps`` output is valid TOML."""
    return json.dumps(value)


def _config_toml_text(cfg: WorldConfig) -> str:
    """Serialize ``cfg`` to TOML (for runs launched without a source file).

    Emits exactly the WorldConfig surface; the caller asserts the round trip
    (``load_world_config`` of the output has the same ``config_sha``).
    """
    w, q, e = cfg.world, cfg.qi, cfg.economy
    lines = [
        "# lamarck run config - synthesized from the in-memory WorldConfig",
        "# (run_sim received no config_path; reload round-trip is asserted).",
        "",
        "[world]",
        f"name = {_toml_string(w.name)}",
        f"master_seed = {_toml_string(w.master_seed)}",
        f"days = {w.days}",
        f"rounds_per_day = {w.rounds_per_day}",
        f"ticks_per_agent_per_round = {w.ticks_per_agent_per_round}",
        "",
        "[population]",
        f"founders = {cfg.population.founders}",
        "",
        "[qi]",
        f"qi_max = {q.qi_max}",
        f"daily_allowance = {q.daily_allowance}",
        "",
        "[qi.action_costs]",
        *(f"{a.value} = {q.action_costs[a]}" for a in ActionType),
        "",
        "[economy]",
        f"starting_stones = {e.starting_stones}",
        f"bounties = {list(e.bounties)!r}",
        f"materials = {list(e.materials)!r}",
        f"stub_success_permille = {list(e.stub_success_permille)!r}",
        "",
    ]
    return "\n".join(lines)


def _write_run_config(cfg: WorldConfig, out_dir: Path, config_path: Path | None) -> None:
    """Materialize ``out_dir/config.toml`` (verbatim copy or synthesized)."""
    dest = out_dir / "config.toml"
    if config_path is not None:
        dest.write_bytes(Path(config_path).read_bytes())
        return
    dest.write_text(_config_toml_text(cfg), encoding="utf-8")
    reloaded = load_world_config(dest)
    LMK_ASSERT(
        config_sha(reloaded) == config_sha(cfg),
        "synthesized config.toml does not round-trip to the same config_sha",
        path=str(dest),
    )


def _balances_sha(ledger_dump: dict) -> str:
    """sha256 of the canonical encoding of a LedgerBalances JSON dump."""
    return sha256_hex(canonical_bytes(ledger_dump))


def _emit_action_slot(
    cfg: WorldConfig,
    ledgers: Ledgers,
    policy: StubPolicy,
    universe: random.Random,
    commit: _CommitFn,
    *,
    day: int,
    rnd: int,
    tick: int,
    actor: str,
) -> tuple[bool, bool]:
    """Run one ACTION slot for ``actor``; returns ``(degraded, discovered)``.

    Implements steps 3(a)/(b) of the pinned layout: view -> decision ->
    affordability (allowance first, stones second) -> one ACTION event,
    plus at most one bounty LEDGER_ADJUST after a non-degraded EXPERIMENT.
    """
    bal = ledgers.balances()
    view = StubView(
        day=day,
        round=rnd,
        tick=tick,
        qi=bal.qi[actor],
        stones=bal.stones[actor],
        allowance_left=ledgers.allowance.remaining(actor),
    )
    atype, args = policy.decide(view)
    cost = cfg.qi.action_costs[atype]

    tier = 0
    affordable = ledgers.allowance.can_spend(actor, cost)
    if atype is ActionType.EXPERIMENT:
        tier_arg = args["tier"]
        LMK_ASSERT(
            isinstance(tier_arg, int) and not isinstance(tier_arg, bool) and 1 <= tier_arg <= 5,
            "EXPERIMENT payload tier outside 1..5",
            actor=actor,
            tier=tier_arg,
        )
        tier = int(tier_arg)
        if affordable and bal.stones[actor] < cfg.economy.materials[tier - 1]:
            affordable = False

    if not affordable:
        rest_cost = cfg.qi.action_costs[ActionType.REST]
        payload: dict[str, object] = {
            "type": ActionType.REST.value,
            "degraded": True,
            "wanted": {"type": atype.value, **args},
        }
        if ledgers.allowance.can_spend(actor, rest_cost):
            qi_delta = -rest_cost
        else:
            payload["free"] = True
            qi_delta = 0
        commit(
            EventDraft(
                day=day,
                tick=tick,
                kind=EventKind.ACTION,
                actor=actor,
                payload=payload,
                qi_delta=qi_delta,
            )
        )
        return True, False

    stones_delta = -cfg.economy.materials[tier - 1] if atype is ActionType.EXPERIMENT else 0
    commit(
        EventDraft(
            day=day,
            tick=tick,
            kind=EventKind.ACTION,
            actor=actor,
            payload={"type": atype.value, **args},
            qi_delta=-cost,
            stones_delta=stones_delta,
        )
    )
    if atype is ActionType.EXPERIMENT:
        roll = universe.randrange(1000)
        if roll < cfg.economy.stub_success_permille[tier - 1]:
            commit(
                EventDraft(
                    day=day,
                    tick=tick,
                    kind=EventKind.LEDGER_ADJUST,
                    actor=actor,
                    payload={"reason": "bounty", "tier": tier},
                    stones_delta=cfg.economy.bounties[tier - 1],
                )
            )
            return False, True
    return False, False


def run_sim(
    cfg: WorldConfig,
    out_dir: Path,
    *,
    config_path: Path | None = None,
    difftest_interval: int = 10,
) -> RunSummary:
    """Run the Phase-0 stub world per the pinned layout; return a RunSummary.

    ``out_dir`` is created if needed but must not already hold an event log.
    ``config_path``, when given, is copied byte-verbatim to
    ``out_dir/config.toml`` (callers must pass the file ``cfg`` was loaded
    from, unmodified); when omitted, an equivalent config is synthesized so
    every run directory is self-contained and replayable.
    ``difftest_interval``: run the dusk difftest on days where
    ``day % difftest_interval == 0``; 0 disables per-dusk checks (the final
    unconditional difftest always runs).
    """
    t0 = time.perf_counter()
    LMK_ASSERT(difftest_interval >= 0, "difftest_interval must be >= 0", got=difftest_interval)
    LMK_ASSERT(
        set(cfg.qi.action_costs) == set(ActionType),
        "config action_costs must cover every ActionType",
        present=sorted(a.value for a in cfg.qi.action_costs),
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "events.sqlite3"
    LMK_ASSERT(
        not db_path.exists(),
        "refusing to run into a directory that already holds an event log",
        path=str(db_path),
    )
    _write_run_config(cfg, out_dir, config_path)

    run_id = compute_run_id(cfg)
    rngs = RngStreams(cfg.world.seed_int())
    scheduler = Scheduler(cfg, rngs)
    ledgers = Ledgers(cfg)
    universe = rngs.stream(STUB_UNIVERSE_STREAM)
    agent_ids = [f"a{i}" for i in range(1, cfg.population.founders + 1)]
    policies = {aid: StubPolicy(rngs.stream(f"agent:{aid}")) for aid in agent_ids}

    emitted = 0
    discoveries = 0
    degraded = 0
    days_elapsed = 0
    last_day = 0
    night_tick = -1

    with EventStore(db_path) as store:

        def commit(draft: EventDraft) -> EventRecord:
            """Append, then apply the COMMITTED record (the store's return —
            NFC-normalized truth), never the draft."""
            nonlocal emitted
            rec = store.append(draft)
            ledgers.apply(rec)
            emitted += 1
            return rec

        with store.batch():
            commit(
                EventDraft(
                    day=0,
                    tick=0,
                    kind=EventKind.RUN_STARTED,
                    actor=WORLD_ACTOR,
                    payload={
                        "run_id": run_id,
                        "config_sha": config_sha(cfg),
                        "master_seed": cfg.world.master_seed,
                        "schema_version": SCHEMA_VERSION,
                        "engine_version": __version__,
                    },
                )
            )
            for aid in agent_ids:
                commit(
                    EventDraft(
                        day=0,
                        tick=0,
                        kind=EventKind.AGENT_SPAWNED,
                        actor=WORLD_ACTOR,
                        payload={
                            "agent_id": aid,
                            "qi_max": cfg.qi.qi_max,
                            "starting_stones": cfg.economy.starting_stones,
                        },
                    )
                )

        for day in range(cfg.world.days):
            with store.batch():
                bal = ledgers.balances()
                alive_ids = [a for a in agent_ids if bal.alive[a]]  # spawn order
                commit(
                    EventDraft(
                        day=day,
                        tick=0,
                        kind=EventKind.DAY_STARTED,
                        actor=WORLD_ACTOR,
                        payload={"day": day},
                    )
                )
                current_phase: DayPhase | None = None
                for phase, rnd, tick, actor in scheduler.iter_day(day, alive_ids):
                    if phase is not current_phase:
                        commit(
                            EventDraft(
                                day=day,
                                tick=tick,
                                kind=EventKind.PHASE_STARTED,
                                actor=WORLD_ACTOR,
                                payload={"day": day, "phase": phase.value},
                            )
                        )
                        current_phase = phase
                    if phase is DayPhase.ACTION:
                        was_degraded, discovered = _emit_action_slot(
                            cfg,
                            ledgers,
                            policies[actor],
                            universe,
                            commit,
                            day=day,
                            rnd=rnd,
                            tick=tick,
                            actor=actor,
                        )
                        degraded += 1 if was_degraded else 0
                        discoveries += 1 if discovered else 0
                    elif phase is DayPhase.DUSK:
                        dusk_bal = ledgers.balances()
                        for aid in agent_ids:  # deaths in spawn order
                            if dusk_bal.alive[aid] and dusk_bal.qi[aid] <= 0:
                                commit(
                                    EventDraft(
                                        day=day,
                                        tick=tick,
                                        kind=EventKind.AGENT_DIED,
                                        actor=aid,
                                        payload={"cause": "qi_exhausted"},
                                    )
                                )
                        if difftest_interval > 0 and day % difftest_interval == 0:
                            assert_difftest(ledgers, store.scan(), cfg)
                    elif phase is DayPhase.NIGHT:
                        night_tick = tick
            last_day = day
            days_elapsed = day + 1
            if not any(ledgers.balances().alive.values()):
                break  # all dead; the day still completed dusk and night

        LMK_ASSERT(days_elapsed >= 1, "run finished without completing a day")
        LMK_ASSERT(night_tick >= 0, "last day completed without a night slot")
        final_balances = ledgers.balances()
        final_state_sha = _balances_sha(final_balances.model_dump(mode="json"))
        commit(
            EventDraft(
                day=last_day,
                tick=night_tick,
                kind=EventKind.RUN_FINISHED,
                actor=WORLD_ACTOR,
                payload={"days_elapsed": days_elapsed, "final_state_sha": final_state_sha},
            )
        )
        head_seq, head_hash = store.verify_chain()
        assert_difftest(ledgers, store.scan(), cfg)
        LMK_ASSERT(
            head_seq + 1 == emitted,
            "verified head disagrees with the runner's emission count",
            head_seq=head_seq,
            emitted=emitted,
        )

    alive_count = sum(1 for is_alive in final_balances.alive.values() if is_alive)
    qi_total = sum(final_balances.qi.values())
    stones_total = sum(final_balances.stones.values())
    wall_ms = int((time.perf_counter() - t0) * 1000)
    summary = RunSummary(
        run_id=run_id,
        days_elapsed=days_elapsed,
        events=emitted,
        head_seq=head_seq,
        head_hash=head_hash,
        final_state_sha=final_state_sha,
        alive_count=alive_count,
        qi_total=qi_total,
        stones_total=stones_total,
        discoveries=discoveries,
        degraded=degraded,
        wall_ms=wall_ms,
        out_dir=str(out_dir),
    )
    report = {
        "run_id": run_id,
        "days_elapsed": days_elapsed,
        "events": emitted,
        "head_seq": head_seq,
        "head_hash": head_hash,
        "final_state_sha": final_state_sha,
        "wall_ms": wall_ms,
        "alive_count": alive_count,
        "qi_total": qi_total,
        "stones_total": stones_total,
    }
    (out_dir / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _replay_failure(run_dir: Path, *reasons: str) -> ReplayResult:
    """A replay verdict for failures before any chain state was recovered."""
    return ReplayResult(
        ok=False,
        run_dir=str(run_dir),
        events=0,
        head_seq=-1,
        head_hash=GENESIS_HASH,
        days_elapsed=None,
        recorded_state_sha=None,
        refolded_state_sha=None,
        mismatches=tuple(reasons),
    )


def replay(run_dir: str | Path) -> ReplayResult:
    """Re-verify a finished run directory with zero model of the sim.

    Checks, in order: artifacts exist and load; ``verify_chain`` passes from
    genesis; the verified head equals the last scanned event; exactly one
    RUN_FINISHED exists and is last; an independent ``fold_balances`` refold
    reproduces the recorded ``final_state_sha``. Time-independent and
    RNG-free: nothing here re-runs policy, scheduler, or universe draws —
    the log alone must justify its own summary. Verification failures are
    returned as ``mismatches``, never raised.
    """
    rd = Path(run_dir)
    db_path = rd / "events.sqlite3"
    config_path = rd / "config.toml"
    missing = [p.name for p in (db_path, config_path) if not p.is_file()]
    if missing:
        return _replay_failure(rd, *(f"missing run artifact: {name}" for name in missing))
    try:
        cfg = load_world_config(config_path)
    except (ValueError, OSError) as err:  # ValidationError/TOMLDecodeError are ValueErrors
        return _replay_failure(rd, f"config.toml failed to load: {err}")
    try:
        store = EventStore(db_path)
    except (LamarckAssertionError, sqlite3.Error) as err:
        return _replay_failure(rd, f"event log failed to open: {err}")
    with store:
        try:
            head_seq, head_hash = store.verify_chain()
            events = list(store.scan())
        except (LamarckAssertionError, sqlite3.Error) as err:
            return _replay_failure(rd, f"chain verification failed: {err}")

    mismatches: list[str] = []
    if not events:
        return _replay_failure(rd, "event log is empty")
    last = events[-1]
    if (last.seq, last.hash) != (head_seq, head_hash):
        mismatches.append(
            f"verified head ({head_seq}, {head_hash}) does not match "
            f"last event ({last.seq}, {last.hash})"
        )
    finished = [ev for ev in events if ev.kind is EventKind.RUN_FINISHED]
    if len(finished) != 1:
        mismatches.append(f"expected exactly one run_finished event, found {len(finished)}")
    elif finished[0].seq != last.seq:
        mismatches.append(f"run_finished at seq {finished[0].seq} is not the last event")

    recorded_sha: str | None = None
    days_elapsed: int | None = None
    if finished:
        payload = finished[-1].payload
        sha = payload.get("final_state_sha")
        days = payload.get("days_elapsed")
        recorded_sha = sha if isinstance(sha, str) else None
        days_elapsed = days if isinstance(days, int) and not isinstance(days, bool) else None
        if recorded_sha is None:
            mismatches.append("run_finished payload lacks a string final_state_sha")

    refolded_sha: str | None = None
    try:
        fold = fold_balances(events, cfg)
        refolded_sha = _balances_sha(fold.model_dump(mode="json"))
    except Exception as err:  # a log the fold cannot account for is a verdict, not a crash
        mismatches.append(f"ledger refold failed: {err!r}")
    if recorded_sha is not None and refolded_sha is not None and recorded_sha != refolded_sha:
        mismatches.append(
            f"final_state_sha mismatch: recorded {recorded_sha} != refolded {refolded_sha}"
        )

    return ReplayResult(
        ok=not mismatches,
        run_dir=str(rd),
        events=len(events),
        head_seq=head_seq,
        head_hash=head_hash,
        days_elapsed=days_elapsed,
        recorded_state_sha=recorded_sha,
        refolded_state_sha=refolded_sha,
        mismatches=tuple(mismatches),
    )
