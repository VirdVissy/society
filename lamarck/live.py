"""Phase-1 live runner and its replay verifiers.

``run_live`` drives the live world: a model backend (real mlx or injected
scripted/cached) decides every action, the wuxing universe verifies every
experiment, and every thought is billed from token usage. ``replay_live``
re-verifies a finished live run — shallow (model-free) or deep (re-executing
the whole runner against the recorded LLM_CALL stream). The Phase-0 stub
path (``lamarck.sim``) is untouched and shares no code with this module
beyond the engine subsystems.

``run_id = sha256_hex(utf8(live_config_sha(cfg) + ":" + cfg.world.master_seed))[:12]``
— the LIVE formula fingerprints the FULL extended config via
``live_config_sha`` (the stub formula uses the Phase-0 ``config_sha``).

========================================================================
EVENT LAYOUT (mirrors the stub layout in lamarck/sim.py; the differences
below are pinned — the live golden test freezes the chain head)
========================================================================
1. RUN_STARTED (day 0, tick 0, world) payload keys:
   ``{run_id, config_sha, master_seed, schema_version, engine_version,
   mode, template_version, model_id}`` where ``config_sha`` carries
   ``live_config_sha(cfg)``, ``mode = "live"``, ``template_version`` is
   ``lamarck.mind.prompt.TEMPLATE_VERSION`` and ``model_id`` is
   ``cfg.model.model_id``.
2. AGENT_SPAWNED x founders (day 0, tick 0, world), spawn order =
   ``mind.personas.founder_ids`` truncated to ``population.founders``:
   ``{agent_id, qi_max, starting_stones, name}`` (``name`` is the persona
   display name); zero deltas. Fewer persona cards than founders is user
   error (ValueError).
3. Per day d, inside one ``store.batch()``: DAY_STARTED then the day's
   slots exactly as the stub (PHASE_STARTED at each transition), except:
   - ACTION slots run the COGNITION LOOP below;
   - the DUSK slot inserts per-agent REFLECTION passes (spawn order,
     living agents) BEFORE deaths; deaths (qi <= 0, spawn order) and the
     periodic difftest follow, exactly as the stub.
   Buffered prompt texts flush to the ``llm_texts`` side table right AFTER
   the day's batch commits (TextsStore writes never overlap a batch).
4. Termination, RUN_FINISHED ``{days_elapsed, final_state_sha}`` (canonical
   ``LedgerBalances`` sha), ``verify_chain``, final unconditional difftest:
   all exactly as the stub.
5. ``report.json`` (live difference): written by
   ``lamarck.analysis.report.write_report`` — a DETERMINISTIC pure fold of
   the event log (no wall time anywhere; two writes are byte-identical).
   Wall time lives only in the returned ``LiveRunSummary``.

========================================================================
COGNITION LOOP (one ACTION slot; every payload key set pinned here)
========================================================================
1. Assemble ``PerceptionView`` purely from folds: ledgers (qi/stones/
   allowance), world fold (location/co-present/heard/notes/reflection/
   outcomes), the full task board (every tier's TaskStubs, tier ascending),
   materials/bounties from config. ``enforce_budget`` shrinks it to
   ``model.prompt_budget_chars``; ``build_prompt(render_system(persona),
   render_user(view))`` is the envelope handed to the backend.
2. ALLOWANCE PRE-CHECK (pinned formula): the runner never commits a spend
   the ledger would reject, so BEFORE every model call it requires
       ``allowance_remaining >= qi_llm_cost(ceil(len(prompt)/2), max_tokens)
         + max(qi.action_costs.values())``
   (a worst-case estimate: chars/2 over-approximates input tokens, the
   surcharge term covers whatever action the model may pick). On failure
   the agent does not think this tick: ACTION
   ``{type: "rest", degraded: true, reason: "spent"}`` with the stub's rest
   billing (``-cost(rest)`` when it fits the allowance, else ``free: true``
   and 0) and NO LLM_CALL. The same formula guards the retry call and —
   with ``reflection_max_tokens`` and NO surcharge term — dusk reflections
   (a failed reflection pre-check SKIPS the reflection entirely: no event).
3. Model call: ``seed`` is one ``randrange(2**31)`` draw from rng stream
   ``"llm-seeds"`` — exactly one draw per model call actually made, drawn
   immediately before it (skipped calls consume nothing). Raw output is
   ``sanitize_model_text``-ed, then LLM_CALL commits with payload keys
   ``{purpose, agent, model_id, adapter, prompt_sha256, temp_permille,
   max_tokens, seed, response, usage_in, usage_out}`` (``purpose`` is
   "tick" or "reflection", ``adapter`` is "" in Phase 1) and
   ``qi_delta = -qi_llm_cost(usage_in, usage_out)``, actor = agent. The
   full prompt text is buffered for the ``llm_texts`` side table. The
   COMMITTED record's ``response`` (NFC truth) is what the parser consumes.
4. Parse + SEMANTIC VALIDATION (runner-owned; every failure produces an
   agent-readable reason fed to ``retry_message``):
   - experiment: ``task_id`` must be on the board;
   - travel: ``to`` must be a configured location AND != current (the
     "to != current" rule lives HERE; the world fold only checks
     membership);
   - trade/converse: ``target`` resolves by persona DISPLAY NAME only;
     unknown names fail. Trade additionally requires
     ``world.can_trade(actor_id, target_id)`` and — pinned decision —
     ``stones <= actor's balance`` (an unaffordable trade takes the RETRY
     path with "you have only N spirit stones", never the degrade path:
     the stub funnel prices only allowance and experiment materials, and a
     committed over-balance trade would be a ledger violation).
5. On failure: one billed retry (contracts MAX_ACTION_PARSE_RETRIES) with
   ``user + "\\n\\n" + retry_message(failure)`` re-enveloped (the corrective
   text rides over the char budget by design). A failed pre-check mid-retry
   forfeits immediately. A second failure forfeits: ACTION
   ``{type: "rest", degraded: true, reason: "malformed"}`` billing REST's
   surcharge (same free-rest fallback).
6. On success, the affordability funnel EXACTLY as the stub — (a) allowance
   must fit ``action_costs[type]``, (b) experiment stones must cover
   ``materials[tier-1]`` — degrading to ACTION ``{type: "rest", degraded:
   true, wanted: {type, **args}}`` otherwise. Affordable actions commit as
   ACTION ``{type, **args}`` with ``qi_delta = -cost`` and ``stones_delta``
   = ``-materials[tier-1]`` (experiment) / ``-stones`` (trade) / 0.
7. Post-action, world-emitted events (actor = agent):
   - experiment: ``universe.attempt`` then TASK_ATTEMPT ``{task_id, tier,
     steps, verified, product, step_products, message, first_in_world}``
     (zero deltas; ``first_in_world`` = verified AND no prior verified
     attempt of this task_id by anyone). Verified: LEDGER_ADJUST
     ``{reason: "bounty", tier, first}`` crediting ``bounties[tier-1] *
     (live.first_discovery_multiplier if first else 1)`` stones.
   - trade: LEDGER_ADJUST ``{reason: "trade", from: actor}`` crediting the
     target with the traded stones (actor = target agent id).
Every committed record — markers included — is applied to the live ledgers
AND the world fold, in commit order.

========================================================================
REPLAY
========================================================================
Shallow (``replay_live(run_dir)``): verify_chain from genesis; exactly one
RUN_FINISHED, last, matching the verified head; independent
``fold_balances`` refold reproduces ``final_state_sha``; every TASK_ATTEMPT
re-verifies through a fresh universe built from the run's config (outcome
fields byte-equal; ``message`` = outcome message + the deterministic
``_board_notes_suffix`` recomputed by the same shared helper);
``first_in_world`` flags re-derive from the log;
bounty/trade LEDGER_ADJUST arithmetic and pairing re-check. Model-free.
Deep (``deep=True``): all shallow checks, then RE-EXECUTE ``run_live``
into a throwaway directory with a ``CachedBackend`` serving the recorded
LLM_CALL events in order — each served call LMK_ASSERTs that the re-derived
prompt sha and GenParams match the record — and the final chain heads must
match byte-for-byte. Verification differences are returned in
``mismatches``, never raised.

Determinism: no wall clock in any payload, no set iteration (the
first-in-world set answers membership only), every rng use from named
streams ("scheduler" via the Scheduler, "llm-seeds" here). A live run is a
pure function of (config bytes, persona files, backend responses).
"""

from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, ValidationError

from lamarck import __version__
from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import (
    GENESIS_HASH,
    SCHEMA_VERSION,
    WORLD_ACTOR,
    ActionType,
    DayPhase,
    EventDraft,
    EventKind,
    EventRecord,
    GenParams,
    GenResult,
    LiveWorldConfig,
    ModelBackendP,
    Outcome,
    PerceptionView,
    PersonaCard,
    Submission,
    TaskStub,
    qi_llm_cost,
)
from lamarck.engine import (
    Ledgers,
    RngStreams,
    Scheduler,
    WorldStateFold,
    assert_difftest,
    fold_balances,
    live_config_sha,
    load_live_config,
)
from lamarck.eventstore import EventStore, TextsStore, canonical_bytes, sha256_hex
from lamarck.mind import (
    TEMPLATE_VERSION,
    ParseFailure,
    build_prompt,
    enforce_budget,
    founder_ids,
    load_personas,
    parse_action,
    prompt_sha,
    render_reflection_user,
    render_system,
    render_user,
    retry_message,
)
from lamarck.serving import make_backend, sanitize_model_text
from lamarck.universes import WuxingUniverse

__all__ = [
    "LLM_SEEDS_STREAM",
    "CachedBackend",
    "LiveReplayResult",
    "LiveRunSummary",
    "compute_live_run_id",
    "replay_live",
    "run_live",
]

LLM_SEEDS_STREAM = "llm-seeds"
_SEED_BOUND = 2**31


class LiveRunSummary(BaseModel):
    """What ``run_live`` returns. Ints and strings only; ``wall_ms`` is the
    ONLY non-deterministic field (report.json never contains it)."""

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
    attempts: int  # TASK_ATTEMPT events committed
    discoveries: int  # verified TASK_ATTEMPT events
    distinct_discoveries: int  # distinct verified task_ids
    first_discoveries: int  # first_in_world discoveries
    degraded: int  # every degraded ACTION (spent + malformed + wanted)
    spent_skips: int  # skip-think degradations (reason "spent")
    malformed_forfeits: int  # forfeits (reason "malformed")
    retries: int  # retry model calls actually made
    retry_recovered: int  # retries that parsed AND validated
    reflections: int
    reflections_skipped: int
    llm_calls: int
    usage_in_total: int
    usage_out_total: int
    wall_ms: int
    out_dir: str


class LiveReplayResult(BaseModel):
    """Verdict of a live replay. ``ok`` iff ``mismatches`` is empty;
    verification differences are reported, never raised."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    run_dir: str
    mode: str  # "shallow" | "deep"
    events: int
    head_seq: int
    head_hash: str
    days_elapsed: int | None
    recorded_state_sha: str | None
    refolded_state_sha: str | None
    attempts_checked: int
    replayed_head_seq: int | None
    replayed_head_hash: str | None
    mismatches: tuple[str, ...]


def compute_live_run_id(cfg: LiveWorldConfig) -> str:
    """First 12 hex chars of sha256 over ``live_config_sha(cfg) + ":" +
    master_seed`` — the live-mode run id (pinned; see module docstring)."""
    return sha256_hex((live_config_sha(cfg) + ":" + cfg.world.master_seed).encode("utf-8"))[:12]


def _repo_personas_dir() -> Path:
    """The repo's ``personas/`` directory (default persona source)."""
    return Path(__file__).resolve().parents[1] / "personas"


class CachedBackend:
    """Deep-replay backend: serves a run's recorded LLM_CALL events in seq
    order. Each ``generate`` pops the next record, LMK_ASSERTs that the
    re-executed runner's prompt sha and GenParams match what was recorded,
    and returns the recorded response and usage. Exhaustion (the re-run
    asking for MORE calls than were recorded) is an assertion failure."""

    def __init__(self, records: list[EventRecord]) -> None:
        for rec in records:
            LMK_ASSERT(
                rec.kind is EventKind.LLM_CALL,
                "CachedBackend takes LLM_CALL records only",
                seq=rec.seq,
                kind=str(rec.kind),
            )
        self._records = list(records)
        self._next = 0

    @property
    def exhausted(self) -> bool:
        """True when every recorded call has been served."""
        return self._next >= len(self._records)

    @property
    def served(self) -> int:
        return self._next

    @property
    def total(self) -> int:
        return len(self._records)

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        LMK_ASSERT(
            self._next < len(self._records),
            "deep replay requested more model calls than were recorded",
            recorded=len(self._records),
        )
        rec = self._records[self._next]
        self._next += 1
        payload = rec.payload
        got_sha = prompt_sha(prompt)
        LMK_ASSERT(
            got_sha == payload.get("prompt_sha256"),
            "deep replay prompt_sha mismatch",
            seq=rec.seq,
            recorded=payload.get("prompt_sha256"),
            re_derived=got_sha,
        )
        recorded_params = (
            payload.get("max_tokens"),
            payload.get("temp_permille"),
            payload.get("seed"),
        )
        LMK_ASSERT(
            recorded_params == (params.max_tokens, params.temp_permille, params.seed),
            "deep replay GenParams mismatch",
            seq=rec.seq,
            recorded=recorded_params,
            re_derived=(params.max_tokens, params.temp_permille, params.seed),
        )
        return GenResult(
            text=_payload_str(payload, "response", rec.seq),
            usage_in=_payload_int(payload, "usage_in", rec.seq),
            usage_out=_payload_int(payload, "usage_out", rec.seq),
        )


# --------------------------------------------------------------- payload utils


def _payload_str(payload: dict[str, Any], key: str, seq: int) -> str:
    value = payload.get(key)
    LMK_ASSERT(isinstance(value, str), f"payload needs a str {key!r}", seq=seq)
    assert isinstance(value, str)
    return value


def _payload_int(payload: dict[str, Any], key: str, seq: int) -> int:
    value = payload.get(key)
    LMK_ASSERT(
        isinstance(value, int) and not isinstance(value, bool),
        f"payload needs an int {key!r}",
        seq=seq,
    )
    assert isinstance(value, int)
    return value


def _ceil_div(n: int, d: int) -> int:
    """Integer ceil(n/d); no float ever materializes."""
    return -(-n // d)


def _balances_sha(ledger_dump: dict[str, Any]) -> str:
    """sha256 of the canonical encoding of a LedgerBalances JSON dump."""
    return sha256_hex(canonical_bytes(ledger_dump))


# ---------------------------------------------------------- config synthesis


def _toml_str(value: str) -> str:
    """TOML basic string via JSON escaping (a strict subset of TOML)."""
    import json

    return json.dumps(value)


def _toml_str_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def _live_config_toml_text(cfg: LiveWorldConfig) -> str:
    """Serialize a LiveWorldConfig to TOML (runs launched without a source
    file); the caller asserts the ``live_config_sha`` round trip."""
    w, q, e, m, u, lv = cfg.world, cfg.qi, cfg.economy, cfg.model, cfg.universe, cfg.live
    lines = [
        "# lamarck live run config - synthesized from the in-memory LiveWorldConfig",
        "# (run_live received no config_path; reload round-trip is asserted).",
        "",
        "[world]",
        f"name = {_toml_str(w.name)}",
        f"master_seed = {_toml_str(w.master_seed)}",
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
        "[model]",
        f"backend = {_toml_str(m.backend)}",
        f"model_id = {_toml_str(m.model_id)}",
        f"max_tokens = {m.max_tokens}",
        f"reflection_max_tokens = {m.reflection_max_tokens}",
        f"temp_permille = {m.temp_permille}",
        f"seed = {m.seed}",
        f"prompt_budget_chars = {m.prompt_budget_chars}",
        "",
        "[universe]",
        f"name = {_toml_str(u.name)}",
        f"seed = {_toml_str(u.seed)}",
        f"tiers = {u.tiers}",
        "",
        "[live]",
        f"locations = {_toml_str_list(list(lv.locations))}",
        f"first_discovery_multiplier = {lv.first_discovery_multiplier}",
        "",
    ]
    return "\n".join(lines)


def _write_run_config(cfg: LiveWorldConfig, out_dir: Path, config_path: Path | None) -> None:
    """Materialize ``out_dir/config.toml`` (verbatim copy or synthesized)."""
    dest = out_dir / "config.toml"
    if config_path is not None:
        dest.write_bytes(Path(config_path).read_bytes())
        return
    dest.write_text(_live_config_toml_text(cfg), encoding="utf-8")
    reloaded = load_live_config(dest)
    LMK_ASSERT(
        live_config_sha(reloaded) == live_config_sha(cfg),
        "synthesized config.toml does not round-trip to the same live_config_sha",
        path=str(dest),
    )


# ----------------------------------------------------------------- the engine


class _Verdict(NamedTuple):
    """Outcome of parse + semantic validation for one model reply."""

    failure: ParseFailure | None
    atype: ActionType
    args: dict[str, object]
    tier: int  # experiment task tier (0 otherwise)
    target_id: str  # trade target agent id ("" otherwise)


_NO_ACTION = _Verdict(None, ActionType.REST, {}, 0, "")


class _LiveEngine:
    """Mutable run state + the cognition loop (internal to run_live)."""

    def __init__(
        self,
        cfg: LiveWorldConfig,
        store: EventStore,
        texts: TextsStore,
        backend: ModelBackendP,
        personas: dict[str, PersonaCard],
        agent_ids: list[str],
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.texts = texts
        self.backend = backend
        self.personas = personas
        self.agent_ids = agent_ids  # spawn order
        self.name_to_id = {personas[aid].name: aid for aid in agent_ids}
        self.ledgers = Ledgers(cfg)
        self.world = WorldStateFold(cfg)
        self.rngs = RngStreams(cfg.world.seed_int())
        self.scheduler = Scheduler(cfg, self.rngs)
        self.llm_seeds = self.rngs.stream(LLM_SEEDS_STREAM)
        self.universe = WuxingUniverse(cfg.universe.seed, cfg.universe.tiers)
        self.board: list[TaskStub] = [
            stub for tier in range(1, cfg.universe.tiers + 1) for stub in self.universe.tasks(tier)
        ]
        self.task_by_id = {stub.task_id: stub for stub in self.board}
        self.task_by_product = _task_by_product(self.board)
        self.max_surcharge = max(cfg.qi.action_costs.values())
        self.first_verified: set[str] = set()  # membership only; never iterated
        # The board honors each commission once per cultivator: repeat
        # verifications by the same agent pay nothing (kills bounty farming,
        # keeps cross-agent verification worth base pay). Membership only.
        self.bounties_paid: set[tuple[str, str]] = set()
        self.texts_buffer: list[tuple[int, str]] = []
        self.emitted = 0
        self.night_tick = -1
        # counters (mirrored into LiveRunSummary)
        self.attempts = 0
        self.discoveries = 0
        self.first_discoveries = 0
        self.degraded = 0
        self.spent_skips = 0
        self.malformed_forfeits = 0
        self.retries = 0
        self.retry_recovered = 0
        self.reflections = 0
        self.reflections_skipped = 0
        self.llm_calls = 0
        self.usage_in_total = 0
        self.usage_out_total = 0
        # dashboard-facing state (display only; never feeds decisions)
        self.last_action: dict[str, str] = dict.fromkeys(agent_ids, "-")
        self.discoveries_by_agent: dict[str, int] = dict.fromkeys(agent_ids, 0)

    # ------------------------------------------------------------- committing

    def commit(self, draft: EventDraft) -> EventRecord:
        """Append, then apply the COMMITTED record (NFC truth) to the live
        ledgers AND the world fold, in commit order."""
        rec = self.store.append(draft)
        self.ledgers.apply(rec)
        self.world.apply(rec)
        self.emitted += 1
        return rec

    # ------------------------------------------------------------------ spawn

    def spawn(self, run_id: str) -> None:
        self.commit(
            EventDraft(
                day=0,
                tick=0,
                kind=EventKind.RUN_STARTED,
                actor=WORLD_ACTOR,
                payload={
                    "run_id": run_id,
                    "config_sha": live_config_sha(self.cfg),
                    "master_seed": self.cfg.world.master_seed,
                    "schema_version": SCHEMA_VERSION,
                    "engine_version": __version__,
                    "mode": "live",
                    "template_version": TEMPLATE_VERSION,
                    "model_id": self.cfg.model.model_id,
                },
            )
        )
        for aid in self.agent_ids:
            self.commit(
                EventDraft(
                    day=0,
                    tick=0,
                    kind=EventKind.AGENT_SPAWNED,
                    actor=WORLD_ACTOR,
                    payload={
                        "agent_id": aid,
                        "qi_max": self.cfg.qi.qi_max,
                        "starting_stones": self.cfg.economy.starting_stones,
                        "name": self.personas[aid].name,
                    },
                )
            )

    # ------------------------------------------------------------- perception

    def view(self, actor: str, day: int, rnd: int, tick: int) -> PerceptionView:
        bal = self.ledgers.balances()
        return PerceptionView(
            day=day,
            round=rnd,
            tick=tick,
            persona=self.personas[actor],
            location=self.world.location(actor),
            locations=list(self.cfg.live.locations),
            qi=bal.qi[actor],
            stones=bal.stones[actor],
            allowance_left=self.ledgers.allowance.remaining(actor),
            co_present=self.world.co_present(actor),
            heard=self.world.heard(actor, day),
            notes=self.world.notes(actor),
            reflection=self.world.reflection(actor),
            outcomes=self.world.outcomes(actor),
            tasks=list(self.board),
            materials=list(self.cfg.economy.materials),
            bounties=list(self.cfg.economy.bounties),
        )

    # ------------------------------------------------------------ model calls

    def precheck(self, actor: str, prompt_chars: int, max_tokens: int, surcharge: int) -> bool:
        """The pinned worst-case allowance pre-check (module docstring §2)."""
        est = qi_llm_cost(_ceil_div(prompt_chars, 2), max_tokens) + surcharge
        return self.ledgers.allowance.remaining(actor) >= est

    def model_call(
        self, actor: str, prompt: str, purpose: str, max_tokens: int, day: int, tick: int
    ) -> EventRecord:
        """One billed model call: seed draw, generate, sanitize, commit
        LLM_CALL, buffer the prompt text. Returns the committed record."""
        seed = self.llm_seeds.randrange(_SEED_BOUND)
        params = GenParams(
            max_tokens=max_tokens, temp_permille=self.cfg.model.temp_permille, seed=seed
        )
        result = self.backend.generate(prompt, params)
        text = sanitize_model_text(result.text)
        rec = self.commit(
            EventDraft(
                day=day,
                tick=tick,
                kind=EventKind.LLM_CALL,
                actor=actor,
                payload={
                    "purpose": purpose,
                    "agent": actor,
                    "model_id": self.cfg.model.model_id,
                    "adapter": "",
                    "prompt_sha256": prompt_sha(prompt),
                    "temp_permille": self.cfg.model.temp_permille,
                    "max_tokens": max_tokens,
                    "seed": seed,
                    "response": text,
                    "usage_in": result.usage_in,
                    "usage_out": result.usage_out,
                },
                qi_delta=-qi_llm_cost(result.usage_in, result.usage_out),
            )
        )
        self.texts_buffer.append((rec.seq, prompt))
        self.llm_calls += 1
        self.usage_in_total += result.usage_in
        self.usage_out_total += result.usage_out
        return rec

    def flush_texts(self) -> None:
        """Write buffered prompt texts; MUST be called outside batch()."""
        for seq, prompt in self.texts_buffer:
            self.texts.put(seq, prompt)
        self.texts_buffer.clear()

    # ------------------------------------------------------------- validation

    def validate_reply(self, actor: str, response: str) -> _Verdict:
        """Parse one committed reply and validate its args semantically."""
        parsed = parse_action(response)
        if isinstance(parsed, ParseFailure):
            return _Verdict(parsed, ActionType.REST, {}, 0, "")
        atype, args = parsed
        if atype is ActionType.EXPERIMENT:
            task_id = args["task_id"]
            assert isinstance(task_id, str)  # parser-guaranteed
            task = self.task_by_id.get(task_id)
            if task is None:
                return self._fail(f"there is no commission {task_id!r} on the task board")
            return _Verdict(None, atype, args, task.tier, "")
        if atype is ActionType.TRAVEL:
            to = args["to"]
            assert isinstance(to, str)  # parser-guaranteed
            if to not in self.cfg.live.locations:
                return self._fail(f"there is no place called {to!r}")
            if to == self.world.location(actor):
                return self._fail(f"you are already at {to}")
            return _Verdict(None, atype, args, 0, "")
        if atype is ActionType.TRADE:
            target = args["target"]
            stones = args["stones"]
            assert isinstance(target, str)  # parser-guaranteed
            assert isinstance(stones, int)  # parser-guaranteed
            target_id = self.name_to_id.get(target)
            if target_id is None:
                return self._fail(f"there is no one called {target!r}")
            ok, reason = self.world.can_trade(actor, target_id)
            if not ok:
                return self._fail(reason)
            balance = self.ledgers.balances().stones[actor]
            if stones > balance:
                return self._fail(f"you have only {balance} spirit stones")
            return _Verdict(None, atype, args, 0, target_id)
        if atype is ActionType.CONVERSE:
            target = args["target"]
            assert isinstance(target, str)  # parser-guaranteed
            if target not in self.name_to_id:
                return self._fail(f"there is no one called {target!r}")
            return _Verdict(None, atype, args, 0, "")
        return _Verdict(None, atype, args, 0, "")

    @staticmethod
    def _fail(reason: str) -> _Verdict:
        return _Verdict(ParseFailure(reason=reason), ActionType.REST, {}, 0, "")

    # ------------------------------------------------------------ action slot

    def emit_degraded_rest(self, day: int, tick: int, actor: str, extra: dict[str, Any]) -> None:
        """The stub's degrade shape: rest surcharge when it fits the
        allowance, else a free rest (``free: true``)."""
        payload: dict[str, Any] = {"type": ActionType.REST.value, "degraded": True, **extra}
        rest_cost = self.cfg.qi.action_costs[ActionType.REST]
        if self.ledgers.allowance.can_spend(actor, rest_cost):
            qi_delta = -rest_cost
        else:
            payload["free"] = True
            qi_delta = 0
        self.commit(
            EventDraft(
                day=day,
                tick=tick,
                kind=EventKind.ACTION,
                actor=actor,
                payload=payload,
                qi_delta=qi_delta,
            )
        )
        self.degraded += 1

    def action_slot(self, day: int, rnd: int, tick: int, actor: str) -> None:
        """One cognition-loop ACTION slot (module docstring, pinned)."""
        cfg = self.cfg
        view = enforce_budget(self.view(actor, day, rnd, tick), cfg.model.prompt_budget_chars)
        system = render_system(self.personas[actor])
        user = render_user(view)
        prompt = build_prompt(system, user)

        if not self.precheck(actor, len(prompt), cfg.model.max_tokens, self.max_surcharge):
            self.emit_degraded_rest(day, tick, actor, {"reason": "spent"})
            self.spent_skips += 1
            self.last_action[actor] = "rest (spent)"
            return

        rec = self.model_call(actor, prompt, "tick", cfg.model.max_tokens, day, tick)
        verdict = self.validate_reply(actor, _payload_str(rec.payload, "response", rec.seq))

        if verdict.failure is not None:
            retry_user = user + "\n\n" + retry_message(verdict.failure)
            retry_prompt = build_prompt(system, retry_user)
            if not self.precheck(
                actor, len(retry_prompt), cfg.model.max_tokens, self.max_surcharge
            ):
                self.emit_degraded_rest(day, tick, actor, {"reason": "malformed"})
                self.malformed_forfeits += 1
                self.last_action[actor] = "rest (malformed)"
                return
            self.retries += 1
            rec = self.model_call(actor, retry_prompt, "tick", cfg.model.max_tokens, day, tick)
            verdict = self.validate_reply(actor, _payload_str(rec.payload, "response", rec.seq))
            if verdict.failure is not None:
                self.emit_degraded_rest(day, tick, actor, {"reason": "malformed"})
                self.malformed_forfeits += 1
                self.last_action[actor] = "rest (malformed)"
                return
            self.retry_recovered += 1

        atype, args = verdict.atype, verdict.args
        cost = cfg.qi.action_costs[atype]
        affordable = self.ledgers.allowance.can_spend(actor, cost)
        stones_delta = 0
        if atype is ActionType.EXPERIMENT:
            materials_cost = cfg.economy.materials[verdict.tier - 1]
            if affordable and self.ledgers.balances().stones[actor] < materials_cost:
                affordable = False
            stones_delta = -materials_cost
        elif atype is ActionType.TRADE:
            traded = args["stones"]
            assert isinstance(traded, int)  # parser-guaranteed
            stones_delta = -traded

        if not affordable:
            self.emit_degraded_rest(day, tick, actor, {"wanted": {"type": atype.value, **args}})
            self.last_action[actor] = f"rest (wanted {atype.value})"
            return

        self.commit(
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
        self.last_action[actor] = atype.value

        if atype is ActionType.EXPERIMENT:
            self._experiment_followup(day, tick, actor, verdict, args)
        elif atype is ActionType.TRADE:
            traded = args["stones"]
            assert isinstance(traded, int)  # parser-guaranteed
            self.commit(
                EventDraft(
                    day=day,
                    tick=tick,
                    kind=EventKind.LEDGER_ADJUST,
                    actor=verdict.target_id,
                    payload={"reason": "trade", "from": actor},
                    stones_delta=traded,
                )
            )

    def _experiment_followup(
        self, day: int, tick: int, actor: str, verdict: _Verdict, args: dict[str, object]
    ) -> None:
        """TASK_ATTEMPT (+ bounty LEDGER_ADJUST on verification)."""
        task_id = args["task_id"]
        assert isinstance(task_id, str)  # parser-guaranteed
        submission = Submission.model_validate({"steps": args["steps"]})
        outcome: Outcome = self.universe.attempt(task_id, submission)
        LMK_ASSERT(
            outcome.tier == verdict.tier,
            "universe outcome tier disagrees with the task board",
            task_id=task_id,
            board=verdict.tier,
            outcome=outcome.tier,
        )
        first = outcome.verified and task_id not in self.first_verified
        message = outcome.message + _board_notes_suffix(outcome.step_products, self.task_by_product)
        self.commit(
            EventDraft(
                day=day,
                tick=tick,
                kind=EventKind.TASK_ATTEMPT,
                actor=actor,
                payload={
                    "task_id": task_id,
                    "tier": outcome.tier,
                    "steps": args["steps"],
                    "verified": outcome.verified,
                    "product": outcome.product,
                    "step_products": list(outcome.step_products),
                    "message": message,
                    "first_in_world": first,
                },
            )
        )
        self.attempts += 1
        self.last_action[actor] = f"experiment {task_id} {'✓' if outcome.verified else '✗'}"
        if outcome.verified:
            if (actor, task_id) not in self.bounties_paid:
                multiplier = self.cfg.live.first_discovery_multiplier if first else 1
                self.commit(
                    EventDraft(
                        day=day,
                        tick=tick,
                        kind=EventKind.LEDGER_ADJUST,
                        actor=actor,
                        payload={"reason": "bounty", "tier": outcome.tier, "first": first},
                        stones_delta=self.cfg.economy.bounties[outcome.tier - 1] * multiplier,
                    )
                )
                self.bounties_paid.add((actor, task_id))
            self.discoveries += 1
            self.discoveries_by_agent[actor] += 1
            if first:
                self.first_discoveries += 1
                self.first_verified.add(task_id)

    # ------------------------------------------------------------------- dusk

    def dusk(self, day: int, tick: int, difftest_interval: int) -> None:
        """Reflections (spawn order, living, skip-when-broke), then deaths,
        then the periodic difftest — the pinned dusk order."""
        cfg = self.cfg
        alive_at_dusk = self.ledgers.balances().alive
        for aid in self.agent_ids:
            if not alive_at_dusk[aid]:
                continue
            view = enforce_budget(self.view(aid, day, 0, tick), cfg.model.prompt_budget_chars)
            prompt = build_prompt(render_system(self.personas[aid]), render_reflection_user(view))
            if not self.precheck(aid, len(prompt), cfg.model.reflection_max_tokens, 0):
                self.reflections_skipped += 1
                continue
            rec = self.model_call(
                aid, prompt, "reflection", cfg.model.reflection_max_tokens, day, tick
            )
            self.commit(
                EventDraft(
                    day=day,
                    tick=tick,
                    kind=EventKind.REFLECTION,
                    actor=aid,
                    payload={"text": _payload_str(rec.payload, "response", rec.seq)},
                )
            )
            self.reflections += 1
        dusk_bal = self.ledgers.balances()
        for aid in self.agent_ids:  # deaths in spawn order, AFTER reflections
            if dusk_bal.alive[aid] and dusk_bal.qi[aid] <= 0:
                self.commit(
                    EventDraft(
                        day=day,
                        tick=tick,
                        kind=EventKind.AGENT_DIED,
                        actor=aid,
                        payload={"cause": "qi_exhausted"},
                    )
                )
        if difftest_interval > 0 and day % difftest_interval == 0:
            assert_difftest(self.ledgers, self.store.scan(), cfg)

    # -------------------------------------------------------------- day loop

    def run_day(self, day: int, difftest_interval: int) -> None:
        """One full day inside one batch (texts flush AFTER, by the caller)."""
        with self.store.batch():
            bal = self.ledgers.balances()
            alive_ids = [a for a in self.agent_ids if bal.alive[a]]  # spawn order
            self.commit(
                EventDraft(
                    day=day,
                    tick=0,
                    kind=EventKind.DAY_STARTED,
                    actor=WORLD_ACTOR,
                    payload={"day": day},
                )
            )
            current_phase: DayPhase | None = None
            for phase, rnd, tick, actor in self.scheduler.iter_day(day, alive_ids):
                if phase is not current_phase:
                    self.commit(
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
                    self.action_slot(day, rnd, tick, actor)
                elif phase is DayPhase.DUSK:
                    self.dusk(day, tick, difftest_interval)
                elif phase is DayPhase.NIGHT:
                    self.night_tick = tick
        self.flush_texts()

    def any_alive(self) -> bool:
        return any(self.ledgers.balances().alive.values())


# ------------------------------------------------------------------ dashboard


def _render_dashboard(engine: _LiveEngine, day: int) -> Any:
    """A per-day rich table (display only; reads folds, never mutates)."""
    from rich.table import Table

    bal = engine.ledgers.balances()
    table = Table(title=f"lamarck live — day {day}")
    for col in ("agent", "location", "qi", "stones", "last action", "discoveries"):
        table.add_column(col)
    for aid in engine.agent_ids:
        name = engine.personas[aid].name
        status = name if bal.alive[aid] else f"[dim]{name} (dead)[/dim]"
        table.add_row(
            status,
            engine.world.location(aid),
            str(bal.qi[aid]),
            str(bal.stones[aid]),
            engine.last_action[aid],
            str(engine.discoveries_by_agent[aid]),
        )
    return table


# ------------------------------------------------------------------- run_live


def run_live(
    cfg: LiveWorldConfig,
    out_dir: Path,
    *,
    config_path: Path | None,
    backend: ModelBackendP | None = None,
    difftest_interval: int = 10,
    dashboard: bool = False,
    personas_dir: Path | None = None,
) -> LiveRunSummary:
    """Run the Phase-1 live world per the pinned layout; return the summary.

    ``backend`` None constructs one from ``cfg.model`` via ``make_backend``
    (the mlx path; tests always inject). ``config_path``, when given, is
    copied byte-verbatim into the run dir; when None an equivalent live TOML
    is synthesized (round-trip asserted). ``personas_dir`` defaults to the
    repo's ``personas/``; too few persona cards for ``population.founders``
    is user error (ValueError). ``dashboard`` renders a per-day rich table
    (display only; OFF in tests).
    """
    t0 = time.perf_counter()
    LMK_ASSERT(difftest_interval >= 0, "difftest_interval must be >= 0", got=difftest_interval)
    LMK_ASSERT(
        set(cfg.qi.action_costs) == set(ActionType),
        "config action_costs must cover every ActionType",
        present=sorted(a.value for a in cfg.qi.action_costs),
    )
    if cfg.universe.name != "wuxing":
        raise ValueError(f"unknown universe {cfg.universe.name!r}; only 'wuxing' runs in Phase 1")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "events.sqlite3"
    LMK_ASSERT(
        not db_path.exists(),
        "refusing to run into a directory that already holds an event log",
        path=str(db_path),
    )
    _write_run_config(cfg, out_dir, config_path)

    cards = load_personas(personas_dir if personas_dir is not None else _repo_personas_dir())
    ordered_ids = founder_ids(cards)
    if len(ordered_ids) < cfg.population.founders:
        raise ValueError(
            f"population.founders = {cfg.population.founders} but only "
            f"{len(ordered_ids)} persona cards were found"
        )
    agent_ids = ordered_ids[: cfg.population.founders]
    personas = {aid: cards[aid] for aid in agent_ids}

    resolved_backend: ModelBackendP = backend if backend is not None else make_backend(cfg.model)
    run_id = compute_live_run_id(cfg)

    with EventStore(db_path) as store, TextsStore(db_path) as texts:
        engine = _LiveEngine(cfg, store, texts, resolved_backend, personas, agent_ids)
        with store.batch():
            engine.spawn(run_id)
        return _drive_to_completion(
            engine,
            cfg,
            store,
            out_dir,
            run_id,
            t0,
            start_day=0,
            difftest_interval=difftest_interval,
            dashboard=dashboard,
        )


def _drive_to_completion(
    engine: _LiveEngine,
    cfg: LiveWorldConfig,
    store: EventStore,
    out_dir: Path,
    run_id: str,
    t0: float,
    *,
    start_day: int,
    difftest_interval: int,
    dashboard: bool,
) -> LiveRunSummary:
    """Run days ``start_day .. cfg.world.days - 1``, then finalize.

    Shared by ``run_live`` (start_day 0, fresh engine) and ``resume_live``
    (start_day = first uncompleted day, engine rebuilt from the log). When
    ``start_day`` leaves no days to run — or the log's last completed day
    ended with nobody alive — the loop is skipped and finalization uses the
    engine's rebuilt ``night_tick``/day state, exactly as a continuous run
    would have finalized.
    """
    days_elapsed = start_day
    last_day = start_day - 1
    if start_day < cfg.world.days and engine.any_alive():
        if dashboard:
            from rich.live import Live

            with Live(refresh_per_second=4) as live_view:
                for day in range(start_day, cfg.world.days):
                    engine.run_day(day, difftest_interval)
                    live_view.update(_render_dashboard(engine, day))
                    last_day = day
                    days_elapsed = day + 1
                    if not engine.any_alive():
                        break
        else:
            for day in range(start_day, cfg.world.days):
                engine.run_day(day, difftest_interval)
                last_day = day
                days_elapsed = day + 1
                if not engine.any_alive():
                    break

    LMK_ASSERT(days_elapsed >= 1, "run finished without completing a day")
    LMK_ASSERT(engine.night_tick >= 0, "last day completed without a night slot")
    final_balances = engine.ledgers.balances()
    final_state_sha = _balances_sha(final_balances.model_dump(mode="json"))
    engine.commit(
        EventDraft(
            day=last_day,  # >= 0 whenever days_elapsed >= 1 (asserted above)
            tick=engine.night_tick,
            kind=EventKind.RUN_FINISHED,
            actor=WORLD_ACTOR,
            payload={"days_elapsed": days_elapsed, "final_state_sha": final_state_sha},
        )
    )
    engine.flush_texts()
    head_seq, head_hash = store.verify_chain()
    assert_difftest(engine.ledgers, store.scan(), cfg)
    LMK_ASSERT(
        head_seq + 1 == engine.emitted,
        "verified head disagrees with the runner's emission count",
        head_seq=head_seq,
        emitted=engine.emitted,
    )

    from lamarck.analysis.report import write_report

    write_report(out_dir)

    alive_count = sum(1 for is_alive in final_balances.alive.values() if is_alive)
    wall_ms = int((time.perf_counter() - t0) * 1000)
    return LiveRunSummary(
        run_id=run_id,
        days_elapsed=days_elapsed,
        events=engine.emitted,
        head_seq=head_seq,
        head_hash=head_hash,
        final_state_sha=final_state_sha,
        alive_count=alive_count,
        qi_total=sum(final_balances.qi.values()),
        stones_total=sum(final_balances.stones.values()),
        attempts=engine.attempts,
        discoveries=engine.discoveries,
        distinct_discoveries=len(engine.first_verified),
        first_discoveries=engine.first_discoveries,
        degraded=engine.degraded,
        spent_skips=engine.spent_skips,
        malformed_forfeits=engine.malformed_forfeits,
        retries=engine.retries,
        retry_recovered=engine.retry_recovered,
        reflections=engine.reflections,
        reflections_skipped=engine.reflections_skipped,
        llm_calls=engine.llm_calls,
        usage_in_total=engine.usage_in_total,
        usage_out_total=engine.usage_out_total,
        wall_ms=wall_ms,
        out_dir=str(out_dir),
    )


# ---------------------------------------------------------------- resume_live


def resume_live(
    run_dir: Path,
    *,
    backend: ModelBackendP | None = None,
    difftest_interval: int = 10,
    dashboard: bool = False,
    personas_dir: Path | None = None,
) -> LiveRunSummary:
    """Continue an interrupted live run from its last completed day.

    A crash mid-day rolls back that whole day (one batch per day), so an
    interrupted log always ends at a clean boundary: the spawn preamble, or
    some day's night marker. Resume rebuilds the ENTIRE engine state by
    folding the log — ledgers, world fold, first-verified/bounties-paid
    sets, counters — and reconstructs both RNG streams exactly (scheduler:
    one ``iter_day`` replay per completed day with that day's alive list;
    llm-seeds: one discarded draw per recorded LLM_CALL), so the continued
    run is byte-indistinguishable from one that never crashed as far as
    deep replay is concerned: ``replay_live(run_dir, deep=True)`` over the
    finished log re-executes days 0..N in one process and must reproduce
    the head.

    Refuses to resume when: the chain fails verification; the run is
    already finished; the log does not end at a clean boundary; the
    config's ``live_config_sha``, the recorded ``template_version``, or the
    spawned persona names disagree with the current code/config/personas
    (a resumed run must not silently mix worlds — deep replay would fail).

    Known cosmetic gap: ``reflections_skipped`` before the crash is
    unrecoverable (skips emit no event by design) and restarts at 0.
    """
    t0 = time.perf_counter()
    run_dir = Path(run_dir)
    db_path = run_dir / "events.sqlite3"
    config_path = run_dir / "config.toml"
    if not db_path.exists() or not config_path.exists():
        raise ValueError(f"{run_dir} is not a run directory (missing events.sqlite3/config.toml)")
    cfg = load_live_config(config_path)

    cards = load_personas(personas_dir if personas_dir is not None else _repo_personas_dir())
    ordered_ids = founder_ids(cards)
    if len(ordered_ids) < cfg.population.founders:
        raise ValueError(
            f"population.founders = {cfg.population.founders} but only "
            f"{len(ordered_ids)} persona cards were found"
        )
    agent_ids = ordered_ids[: cfg.population.founders]
    personas = {aid: cards[aid] for aid in agent_ids}
    run_id = compute_live_run_id(cfg)

    with EventStore(db_path) as store, TextsStore(db_path) as texts:
        store.verify_chain()  # integrity before trusting a single byte
        records = list(store.scan())
        _validate_resumable(records, cfg, run_id, personas, agent_ids)
        # Backend AFTER validation: a doomed resume must never load a model.
        resolved_backend: ModelBackendP = (
            backend if backend is not None else make_backend(cfg.model)
        )
        engine = _LiveEngine(cfg, store, texts, resolved_backend, personas, agent_ids)
        start_day = _rebuild_engine_state(engine, records)
        return _drive_to_completion(
            engine,
            cfg,
            store,
            run_dir,
            run_id,
            t0,
            start_day=start_day,
            difftest_interval=difftest_interval,
            dashboard=dashboard,
        )


def _validate_resumable(
    records: list[EventRecord],
    cfg: LiveWorldConfig,
    run_id: str,
    personas: dict[str, PersonaCard],
    agent_ids: list[str],
) -> None:
    """Refuse-loudly checks; every message states the disagreement found."""
    founders = cfg.population.founders
    if len(records) < 1 + founders:
        raise ValueError("log is shorter than the spawn preamble; nothing to resume")
    head = records[0]
    if head.kind is not EventKind.RUN_STARTED or head.payload.get("mode") != "live":
        raise ValueError("not a live run log (first event is not a live RUN_STARTED)")
    if any(ev.kind is EventKind.RUN_FINISHED for ev in records):
        raise ValueError("run is already finished; nothing to resume")
    if head.payload.get("run_id") != run_id or head.payload.get("config_sha") != live_config_sha(
        cfg
    ):
        raise ValueError(
            "recorded run_id/config_sha disagree with the run dir's config.toml: "
            "the config changed since the run started"
        )
    if head.payload.get("template_version") != TEMPLATE_VERSION:
        raise ValueError(
            f"run was recorded under template {head.payload.get('template_version')!r} but "
            f"the current code renders {TEMPLATE_VERSION!r}: resuming would mix prompt "
            "templates within one run (deep replay would fail)"
        )
    spawns = records[1 : 1 + founders]
    for ev, aid in zip(spawns, agent_ids, strict=True):
        if ev.kind is not EventKind.AGENT_SPAWNED or ev.payload.get("agent_id") != aid:
            raise ValueError("spawn preamble does not match the configured founders")
        if ev.payload.get("name") != personas[aid].name:
            raise ValueError(
                f"agent {aid} was spawned as {ev.payload.get('name')!r} but the current "
                f"persona card says {personas[aid].name!r}: personas changed since the run"
            )
    last = records[-1]
    clean_preamble = len(records) == 1 + founders
    clean_night = last.kind is EventKind.PHASE_STARTED and last.payload.get("phase") == "night"
    if not (clean_preamble or clean_night):
        raise ValueError(
            f"log does not end at a clean day boundary (last event: {last.kind.value} "
            f"day {last.day} tick {last.tick}) — the store is corrupt or foreign"
        )


def _rebuild_engine_state(engine: _LiveEngine, records: list[EventRecord]) -> int:
    """Fold ``records`` into a fresh engine; return the first day to run.

    Mirrors the live path exactly: every record goes through the ledgers and
    world fold in commit order; counters refold from payloads; both RNG
    streams are advanced by replaying their recorded consumption (the
    scheduler consumed ``rounds_per_day`` shuffles per completed day, keyed
    by that day's spawn-order alive list; llm-seeds consumed exactly one
    draw per LLM_CALL committed).
    """
    alive_in_spawn_order = list(engine.agent_ids)
    day_alive: dict[int, list[str]] = {}
    tick_calls: dict[tuple[int, int], int] = {}
    tick_forfeited: set[tuple[int, int]] = set()
    last_day = -1
    for ev in records:
        engine.ledgers.apply(ev)
        engine.world.apply(ev)
        payload = ev.payload
        if ev.kind is EventKind.DAY_STARTED:
            day = _payload_int(payload, "day", ev.seq)
            day_alive[day] = list(alive_in_spawn_order)
            last_day = max(last_day, day)
        elif ev.kind is EventKind.AGENT_DIED:
            alive_in_spawn_order.remove(ev.actor)
        elif ev.kind is EventKind.PHASE_STARTED and payload.get("phase") == "night":
            engine.night_tick = ev.tick
        elif ev.kind is EventKind.LLM_CALL:
            engine.llm_calls += 1
            engine.usage_in_total += _payload_int(payload, "usage_in", ev.seq)
            engine.usage_out_total += _payload_int(payload, "usage_out", ev.seq)
            if payload.get("purpose") == "reflection":
                pass  # reflection calls never retry
            else:
                key = (ev.day, ev.tick)
                tick_calls[key] = tick_calls.get(key, 0) + 1
        elif ev.kind is EventKind.REFLECTION:
            engine.reflections += 1
        elif ev.kind is EventKind.ACTION:
            if payload.get("degraded"):
                engine.degraded += 1
                reason = payload.get("reason")
                if reason == "spent":
                    engine.spent_skips += 1
                elif reason == "malformed":
                    engine.malformed_forfeits += 1
                    tick_forfeited.add((ev.day, ev.tick))
            engine.last_action[ev.actor] = str(payload.get("type", "-"))
        elif ev.kind is EventKind.TASK_ATTEMPT:
            engine.attempts += 1
            task_id = _payload_str(payload, "task_id", ev.seq)
            if payload.get("verified"):
                engine.discoveries += 1
                engine.discoveries_by_agent[ev.actor] += 1
                engine.first_verified.add(task_id)
                engine.bounties_paid.add((ev.actor, task_id))
                if payload.get("first_in_world"):
                    engine.first_discoveries += 1
    retried = [key for key, count in tick_calls.items() if count > 1]
    engine.retries = len(retried)
    engine.retry_recovered = sum(1 for key in retried if key not in tick_forfeited)
    engine.emitted = len(records)

    for day in sorted(day_alive):
        # iter_day consumes the scheduler stream at CALL time (the whole day
        # is precomputed); the returned iterator is deliberately discarded.
        engine.scheduler.iter_day(day, day_alive[day])
    for _ in range(engine.llm_calls):
        engine.llm_seeds.randrange(2**31)
    return last_day + 1


# ---------------------------------------------------------------- replay_live


def _live_replay_failure(run_dir: Path, mode: str, *reasons: str) -> LiveReplayResult:
    """A verdict for failures before any chain state was recovered."""
    return LiveReplayResult(
        ok=False,
        run_dir=str(run_dir),
        mode=mode,
        events=0,
        head_seq=-1,
        head_hash=GENESIS_HASH,
        days_elapsed=None,
        recorded_state_sha=None,
        refolded_state_sha=None,
        attempts_checked=0,
        replayed_head_seq=None,
        replayed_head_hash=None,
        mismatches=tuple(reasons),
    )


def _reverify_attempts(
    events: list[EventRecord], cfg: LiveWorldConfig, mismatches: list[str]
) -> int:
    """Re-verify every TASK_ATTEMPT through a fresh universe, re-derive
    first_in_world flags, and re-check bounty/trade adjust arithmetic and
    pairing. Returns the number of attempts checked."""
    universe = WuxingUniverse(cfg.universe.seed, cfg.universe.tiers)
    task_by_product = _task_by_product(
        stub for tier in range(1, cfg.universe.tiers + 1) for stub in universe.tasks(tier)
    )
    seen_verified: set[str] = set()  # membership only; never iterated
    paid_pairs: set[tuple[str, str]] = set()  # (actor, task_id); once-per-cultivator rule
    checked = 0
    prev: EventRecord | None = None
    for ev in events:
        if ev.kind is EventKind.TASK_ATTEMPT:
            checked += 1
            payload = ev.payload
            task_id = payload.get("task_id")
            if not isinstance(task_id, str):
                mismatches.append(f"task_attempt at seq {ev.seq} lacks a str task_id")
                prev = ev
                continue
            try:
                submission = Submission.model_validate({"steps": payload.get("steps")})
            except ValidationError as err:
                mismatches.append(
                    f"task_attempt at seq {ev.seq} has an invalid steps payload: {err}"
                )
                prev = ev
                continue
            outcome = universe.attempt(task_id, submission)
            expected: dict[str, Any] = {
                "verified": outcome.verified,
                "product": outcome.product,
                "tier": outcome.tier,
                "step_products": list(outcome.step_products),
                "message": outcome.message
                + _board_notes_suffix(outcome.step_products, task_by_product),
            }
            got = {key: payload.get(key) for key in expected}
            if got != expected:
                mismatches.append(
                    f"task_attempt at seq {ev.seq} does not re-verify: "
                    f"recorded {got} != recomputed {expected}"
                )
            expected_first = outcome.verified and task_id not in seen_verified
            if payload.get("first_in_world") != expected_first:
                mismatches.append(
                    f"task_attempt at seq {ev.seq} first_in_world flag is "
                    f"{payload.get('first_in_world')!r}, refold says {expected_first}"
                )
            if outcome.verified:
                seen_verified.add(task_id)
        elif ev.kind is EventKind.LEDGER_ADJUST:
            reason = ev.payload.get("reason")
            if reason == "bounty":
                _check_bounty_adjust(ev, prev, cfg, mismatches)
                if prev is not None and prev.kind is EventKind.TASK_ATTEMPT:
                    pair = (ev.actor, str(prev.payload.get("task_id")))
                    if pair in paid_pairs:
                        mismatches.append(
                            f"bounty adjust at seq {ev.seq} re-pays {pair!r}: the board "
                            "honors each commission once per cultivator"
                        )
                    paid_pairs.add(pair)
            elif reason == "trade":
                _check_trade_adjust(ev, prev, mismatches)
            else:
                mismatches.append(f"ledger_adjust at seq {ev.seq} has unexpected reason {reason!r}")
        prev = ev
    return checked


def _task_by_product(board: Iterable[TaskStub]) -> dict[str, str]:
    """Map product name -> task_id from the PUBLIC board titles.

    Titles are 'produce "<name>"'; the quoted name is extracted. This is a
    cross-reference over information every prompt already shows — it never
    touches hidden rules.
    """
    mapping: dict[str, str] = {}
    for stub in board:
        match = re.search(r'"([^"]+)"', stub.title)
        LMK_ASSERT(match is not None, "task title does not name its product", title=stub.title)
        assert match is not None  # narrow for mypy; guaranteed above
        mapping[match.group(1)] = stub.task_id
    return mapping


def _board_notes_suffix(step_products: Sequence[str], task_by_product: dict[str, str]) -> str:
    """The board's cross-reference appended to attempt messages: which
    commission pays for each product the attempt yielded.

    Joins two facts the prompts already display separately (what you made;
    what the board pays for) — the acceptance-run diagnosis showed agents
    producing every tier-1 compound while filing only 2 commissions because
    this link went unmade. Deterministic: first-appearance order, deduped,
    "slag" and unknown names skipped; empty string when nothing matches.
    The shallow-replay verifier recomputes it with this same function.
    """
    noted: list[str] = []
    seen: set[str] = set()
    for product in step_products:
        if product in seen or product == "slag" or product not in task_by_product:
            continue
        seen.add(product)
        noted.append(f"{product} fulfills {task_by_product[product]}")
    if not noted:
        return ""
    return " The board notes: " + "; ".join(noted) + "."


def _check_bounty_adjust(
    ev: EventRecord, prev: EventRecord | None, cfg: LiveWorldConfig, mismatches: list[str]
) -> None:
    tier = ev.payload.get("tier")
    first = ev.payload.get("first")
    if not (isinstance(tier, int) and not isinstance(tier, bool) and 1 <= tier <= 5):
        mismatches.append(f"bounty adjust at seq {ev.seq} has invalid tier {tier!r}")
        return
    if not isinstance(first, bool):
        mismatches.append(f"bounty adjust at seq {ev.seq} lacks a bool 'first'")
        return
    multiplier = cfg.live.first_discovery_multiplier if first else 1
    expected = cfg.economy.bounties[tier - 1] * multiplier
    if ev.stones_delta != expected:
        mismatches.append(
            f"bounty adjust at seq {ev.seq} credits {ev.stones_delta}, "
            f"config arithmetic says {expected}"
        )
    if (
        prev is None
        or prev.kind is not EventKind.TASK_ATTEMPT
        or prev.actor != ev.actor
        or prev.payload.get("verified") is not True
        or prev.payload.get("tier") != tier
        or prev.payload.get("first_in_world") != first
    ):
        mismatches.append(
            f"bounty adjust at seq {ev.seq} is not paired with its verified task_attempt"
        )


def _check_trade_adjust(ev: EventRecord, prev: EventRecord | None, mismatches: list[str]) -> None:
    if (
        prev is None
        or prev.kind is not EventKind.ACTION
        or prev.payload.get("type") != ActionType.TRADE.value
        or prev.payload.get("degraded")
        or prev.actor != ev.payload.get("from")
        or ev.stones_delta != -prev.stones_delta
    ):
        mismatches.append(
            f"trade adjust at seq {ev.seq} is not paired with a matching trade action"
        )


def replay_live(
    run_dir: str | Path, deep: bool = False, *, personas_dir: Path | None = None
) -> LiveReplayResult:
    """Re-verify a finished LIVE run directory (see module docstring).

    Shallow: chain, RUN_FINISHED shape, ledger refold vs final_state_sha,
    universe re-verification of every TASK_ATTEMPT (plus first_in_world and
    adjust arithmetic refolds). Deep additionally re-executes the runner
    against the recorded LLM_CALL stream via :class:`CachedBackend` and
    compares chain heads byte-for-byte. Never raises for verification
    differences — every finding lands in ``mismatches``.
    """
    mode = "deep" if deep else "shallow"
    rd = Path(run_dir)
    db_path = rd / "events.sqlite3"
    config_path = rd / "config.toml"
    missing = [p.name for p in (db_path, config_path) if not p.is_file()]
    if missing:
        return _live_replay_failure(rd, mode, *(f"missing run artifact: {m}" for m in missing))
    try:
        cfg = load_live_config(config_path)
    except (ValueError, OSError) as err:  # ValidationError/TOMLDecodeError are ValueErrors
        return _live_replay_failure(rd, mode, f"config.toml failed to load as a live config: {err}")
    try:
        with EventStore(db_path) as store:
            head_seq, head_hash = store.verify_chain()
            events = list(store.scan())
    except Exception as err:  # a log that will not verify is a verdict, not a crash
        return _live_replay_failure(rd, mode, f"chain verification failed: {err}")

    mismatches: list[str] = []
    if not events:
        return _live_replay_failure(rd, mode, "event log is empty")
    first_ev = events[0]
    if first_ev.kind is not EventKind.RUN_STARTED or first_ev.payload.get("mode") != "live":
        mismatches.append("first event is not a live-mode run_started")
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
    except Exception as err:  # a log the fold cannot account for is a verdict
        mismatches.append(f"ledger refold failed: {err!r}")
    if recorded_sha is not None and refolded_sha is not None and recorded_sha != refolded_sha:
        mismatches.append(
            f"final_state_sha mismatch: recorded {recorded_sha} != refolded {refolded_sha}"
        )

    try:
        attempts_checked = _reverify_attempts(events, cfg, mismatches)
    except Exception as err:  # ditto: report, never crash
        attempts_checked = 0
        mismatches.append(f"task re-verification failed: {err!r}")

    replayed_head_seq: int | None = None
    replayed_head_hash: str | None = None
    if deep:
        llm_records = [ev for ev in events if ev.kind is EventKind.LLM_CALL]
        cached = CachedBackend(llm_records)
        try:
            with tempfile.TemporaryDirectory(prefix="lamarck-deep-replay-") as td:
                summary = run_live(
                    cfg,
                    Path(td) / "replay",
                    config_path=config_path,
                    backend=cached,
                    difftest_interval=0,
                    dashboard=False,
                    personas_dir=personas_dir,
                )
        except Exception as err:  # assertion inside re-execution => verdict
            mismatches.append(f"deep replay re-execution failed: {err}")
        else:
            replayed_head_seq, replayed_head_hash = summary.head_seq, summary.head_hash
            if (replayed_head_seq, replayed_head_hash) != (head_seq, head_hash):
                mismatches.append(
                    f"deep replay chain head mismatch: recorded ({head_seq}, {head_hash}) "
                    f"!= re-executed ({replayed_head_seq}, {replayed_head_hash})"
                )
            if not cached.exhausted:
                mismatches.append(
                    f"deep replay left {cached.total - cached.served} of "
                    f"{cached.total} recorded model calls unreplayed"
                )

    return LiveReplayResult(
        ok=not mismatches,
        run_dir=str(rd),
        mode=mode,
        events=len(events),
        head_seq=head_seq,
        head_hash=head_hash,
        days_elapsed=days_elapsed,
        recorded_state_sha=recorded_sha,
        refolded_state_sha=refolded_sha,
        attempts_checked=attempts_checked,
        replayed_head_seq=replayed_head_seq,
        replayed_head_hash=replayed_head_hash,
        mismatches=tuple(mismatches),
    )
