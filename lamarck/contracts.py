"""FROZEN CONTRACTS — the coordination surface for Phase 0.

Every subsystem codes against the types and protocols in this file. During a
build wave this file is FROZEN: implementation agents must not edit it. If an
implementation genuinely cannot be built against a contract, the agent reports
the friction in its final summary instead of changing this file; the integrator
amends contracts between waves.

Extracted-to and locked-by docs/SPEC.md at the close of Phase 0.

========================================================================
CANONICAL JSON (implemented in lamarck.eventstore.canonical)
========================================================================
The one true serialized form. Rules (all MUST):
  - dicts with str keys only; values: str | int | bool | None | dict | list
  - floats are REJECTED (raise CanonicalError). Fractions are represented as
    scaled integers by convention (e.g. permille). No NaN/Inf ever.
  - all strings (keys and values) are NFC-normalized before encoding
  - keys sorted (codepoint order after NFC), separators (",", ":"),
    ensure_ascii=False, UTF-8 bytes out
  - no leading/trailing whitespace; encoder is total and deterministic:
    canonical(x) == canonical(x) byte-for-byte, forever, across platforms

========================================================================
HASH CHAIN (implemented in lamarck.eventstore.store)
========================================================================
  envelope_i  = {"seq": i, "day": d, "tick": t, "kind": k, "actor": a,
                 "payload": p, "qi_delta": q, "stones_delta": s}
  hash_i      = sha256_hex( utf8(prev_hash_hex) || canonical_bytes(envelope_i) )
  prev of the first event (seq=0) is GENESIS_HASH (64 zeros).
The chain head (last_seq, last_hash) is the run fingerprint. Wall-clock
timestamps are stored in a side column and are NEVER hashed — replay must be
time-independent.

========================================================================
RNG STREAMS (implemented in lamarck.engine.rng)
========================================================================
Substreams are derived, never shared, never reseeded mid-run:
  name_tag     = first 8 bytes of sha256(utf8(name)), read big-endian -> u64
  stream_seed  = splitmix64(master_seed XOR name_tag)      (one splitmix64 step)
  stream       = random.Random(stream_seed)
splitmix64 is the reference algorithm (Steele et al.); implementation must
match the pinned test vectors in tests/test_rng.py. Canonical stream names in
Phase 0: "scheduler", "stub-universe", and "agent:{agent_id}".

========================================================================
PHASE-1 LIVE SEMANTICS (additive; the Phase-0 stub path is unchanged and its
golden fingerprint must keep passing)
========================================================================
Qi billing moves to real token usage: every model call appends an LLM_CALL
event billing ``qi_delta = -qi_llm_cost(usage_in, usage_out)`` where
``qi_llm_cost = ceil(usage_in / QI_INPUT_DIVISOR) + usage_out`` (thinking
and speaking cost 4x listening). The daily allowance counts negative qi on
BOTH ACTION and LLM_CALL events. ACTION events in live mode carry only
engine-priced surcharges (travel, materials); the thinking is billed on the
LLM_CALL that produced it.

Model text sanitization (before any payload): lone surrogates -> U+FFFD,
NULs stripped. The committed (NFC-normalized) record is the truth consumed
by parsers.

Action protocol: the model must emit one JSON object {"action": <ActionType
value>, ...args} (optionally inside a ``` fence). On a parse/validation
failure the runner retries ONCE (MAX_ACTION_PARSE_RETRIES) with an error
message appended; both calls bill. A second failure forfeits the slot as
ACTION {"type": "rest", "degraded": true, "reason": "malformed"} billing
REST's surcharge. Unaffordable actions degrade exactly as in Phase 0.

Live action args (validated; invalid args take the retry path, unaffordable
takes the degrade path):
  EXPERIMENT {"steps": [[a, b], ...]}  — steps only (auto-claim, 2026-08-05:
      no task_id, no upfront materials charge, stones_delta 0); outcome
      recorded as a TASK_ATTEMPT event (world-emitted, actor = agent, zero
      deltas) {steps, step_products, message, claims: [{task_id, tier,
      first}]}; each claim appends LEDGER_ADJUST {"reason": "bounty",
      "task_id", "tier", "first"} crediting bounties[tier-1] × (live.
      first_discovery_multiplier when first-in-world) − materials[tier-1]
      (materials are paid on delivery; see CraftResult below).
  CONVERSE {"target", "text"}  — open-air speech: heard by every agent
      co-located with the speaker at emission (target is addressing flavor).
      An agent's perception carries the up-to-6 most recent utterances from
      co-located others with day >= current_day - 1.
  TRAVEL {"to"}  — must be a configured location != current.
  TRADE {"target", "stones": > 0}  — target alive and co-located; ACTION
      debits the actor, engine appends LEDGER_ADJUST
      {"reason": "trade", "from": actor} crediting the target.
  NOTE {"text"}  — private memory, <= NOTE_MAX_CHARS chars.
  TEACH/STUDY/CHALLENGE/ATTEMPT_BREAKTHROUGH/MEDITATE/REST {} — Phase-1
      flavor no-ops (billed; real semantics arrive in Phases 2-4).

Dusk (live): each living agent, in spawn order, produces one REFLECTION
event ({"text"}, zero deltas; its LLM_CALL bills) — the rolling
self-summary fed to the next day's prompts. Deaths are checked after
reflections.

Determinism: prompts are pure functions of event-log-derived state
(PerceptionView) rendered by a versioned template; prompt_sha256 goes into
the LLM_CALL payload, full prompt text into an UNHASHED side table. Replay
modes: verify (chain + refold + universe re-verification) and deep
(re-execute the runner against the recorded LLM_CALL stream, asserting each
prompt_sha and reproducing the identical chain head).

========================================================================
PHASE-0 ACTION SEMANTICS (stub world; unchanged, still exercised by tests)
========================================================================
Actions cost flat qi per ActionType (config [qi.action_costs]) — the stand-in
for token metering. Additional rules:
  - EXPERIMENT payload {"tier": 1..5}: additionally requires
    materials[tier-1] stones (config [economy.materials]). If unaffordable
    (stones or remaining daily allowance), the action DEGRADES to REST and the
    action payload records {"degraded": true, "wanted": <original>}.
    Success is drawn from stream "stub-universe": success iff
    next uniform int in [0, 1000) < stub_success_permille[tier-1]. On success
    the engine appends a LEDGER_ADJUST event crediting bounties[tier-1] stones
    (reason "bounty").
  - Daily allowance: an agent's qi spend per day is capped by
    [qi.daily_allowance]; an action that would exceed it degrades to REST.
  - Death: at DUSK, any agent with qi <= 0 dies (AGENT_DIED appended, alive
    flag cleared). Dead agents take no further ticks. The run ends after the
    configured number of days or when all agents are dead, whichever is first.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

# ------------------------------------------------------------------ constants

SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
MASTER_SEED_DEFAULT = 0xDE5EEDDE5EEDDE5E  # house seed family 0xDE5EED…
WORLD_ACTOR = ""  # actor field for world/engine-emitted events


# ---------------------------------------------------------------------- enums


class EventKind(StrEnum):
    RUN_STARTED = "run_started"
    DAY_STARTED = "day_started"
    PHASE_STARTED = "phase_started"
    AGENT_SPAWNED = "agent_spawned"
    ACTION = "action"
    LEDGER_ADJUST = "ledger_adjust"
    AGENT_DIED = "agent_died"
    RUN_FINISHED = "run_finished"
    # Phase 1 (appended; never reorder — the pin test locks the sequence)
    LLM_CALL = "llm_call"
    TASK_ATTEMPT = "task_attempt"
    REFLECTION = "reflection"


class DayPhase(StrEnum):
    DAWN = "dawn"
    ACTION = "action"
    DUSK = "dusk"
    NIGHT = "night"


class ActionType(StrEnum):
    EXPERIMENT = "experiment"
    CONVERSE = "converse"
    TEACH = "teach"
    STUDY = "study"
    TRADE = "trade"
    NOTE = "note"
    TRAVEL = "travel"
    MEDITATE = "meditate"
    CHALLENGE = "challenge"
    ATTEMPT_BREAKTHROUGH = "attempt_breakthrough"
    REST = "rest"


# --------------------------------------------------------------------- events


class EventDraft(BaseModel):
    """What a producer submits; the store assigns seq and hash."""

    model_config = ConfigDict(frozen=True)

    day: int = Field(ge=0)
    tick: int = Field(ge=0)
    kind: EventKind
    actor: str = WORLD_ACTOR
    payload: dict[str, Any] = Field(default_factory=dict)
    qi_delta: int = 0
    stones_delta: int = 0


class EventRecord(EventDraft):
    """A committed, hash-chained event. Immutable."""

    seq: int = Field(ge=0)
    hash: str = Field(min_length=64, max_length=64)


# --------------------------------------------------------------------- config


class WorldSection(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    master_seed: str  # hex string, e.g. "0xDE5EEDDE5EEDDE5E"
    days: int = Field(ge=1)
    rounds_per_day: int = Field(ge=1)
    ticks_per_agent_per_round: int = Field(ge=1, default=1)

    def seed_int(self) -> int:
        return int(self.master_seed, 16)


class PopulationSection(BaseModel):
    model_config = ConfigDict(frozen=True)
    founders: int = Field(ge=1)


class QiSection(BaseModel):
    model_config = ConfigDict(frozen=True)
    qi_max: int = Field(ge=1)
    daily_allowance: int = Field(ge=1)
    action_costs: dict[ActionType, int]  # every ActionType must be present


class EconomySection(BaseModel):
    model_config = ConfigDict(frozen=True)
    starting_stones: int = Field(ge=0)
    bounties: list[int] = Field(min_length=5, max_length=5)
    materials: list[int] = Field(min_length=5, max_length=5)
    stub_success_permille: list[int] = Field(min_length=5, max_length=5)


class WorldConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    world: WorldSection
    population: PopulationSection
    qi: QiSection
    economy: EconomySection


# -------------------------------------------------------------------- ledgers


class LedgerBalances(BaseModel):
    """The difftest surface: live ledgers and the independent fold must agree
    on this exact structure at any event boundary."""

    model_config = ConfigDict(frozen=True)

    qi: dict[str, int]
    stones: dict[str, int]
    alive: dict[str, bool]


# ------------------------------------------------------------------ protocols


class EventStoreP(Protocol):
    """Append-only hash-chained log over SQLite (WAL). Single writer."""

    def append(self, draft: EventDraft) -> EventRecord: ...
    def batch(self) -> AbstractContextManager[None]:
        """Group appends into one transaction (perf; semantics unchanged)."""
        ...

    def scan(self, from_seq: int = 0) -> Iterator[EventRecord]: ...
    def head(self) -> tuple[int, str]:
        """(last_seq, last_hash); (-1, GENESIS_HASH) when empty."""
        ...


class RngStreamsP(Protocol):
    def stream(self, name: str) -> random.Random:
        """Memoized: same name -> same object; derivation per module docstring."""
        ...


class SchedulerP(Protocol):
    def iter_day(self, day: int, alive_ids: list[str]) -> Iterator[TickSlot]:
        """Yield the day's slots in order: one DAWN slot (world), then
        rounds_per_day rounds of per-agent ACTION slots (order = seeded
        shuffle from stream "scheduler", reshuffled each round), then one
        DUSK slot (world), then one NIGHT slot (world). tick indices are
        contiguous from 0 within the day."""
        ...


class LedgersP(Protocol):
    """Live balances. Every mutation arrives as an already-committed event."""

    def apply(self, ev: EventRecord) -> None: ...
    def balances(self) -> LedgerBalances: ...


class StubPolicyP(Protocol):
    def decide(self, view: StubView) -> tuple[ActionType, dict[str, Any]]:
        """Pure function of (view, own rng stream). Must be deterministic."""
        ...


class StubView(BaseModel):
    """Everything a Phase-0 stub agent may condition on."""

    model_config = ConfigDict(frozen=True)

    day: int
    round: int
    tick: int
    qi: int
    stones: int
    allowance_left: int


# The independent fold lives at lamarck.engine.ledgers.fold_balances with the
# exact signature below; the difftest asserts fold == live at run end (and at
# every dusk). eventstore stays generic — it never interprets payloads.
#
#   def fold_balances(events: Iterable[EventRecord], cfg: WorldConfig) -> LedgerBalances


TickSlot = tuple[DayPhase, int, int, str]  # (phase, round, tick, actor_id); world slots actor=""


# ======================================================================
# PHASE 1 — live-world contracts (additive; Phase-0 surface above is frozen)
# ======================================================================

QI_INPUT_DIVISOR = 4  # listening is 4x cheaper than thinking/speaking
MAX_ACTION_PARSE_RETRIES = 1  # one billed retry, then forfeit
NOTE_MAX_CHARS = 500


def qi_llm_cost(usage_in: int, usage_out: int) -> int:
    """The one true qi price of a model call: ceil(in/4) + out (contract)."""
    return -(-usage_in // QI_INPUT_DIVISOR) + usage_out


class GenParams(BaseModel):
    """Sampling parameters — integers only (permille), part of the hashed
    LLM_CALL payload and of the response-cache identity."""

    model_config = ConfigDict(frozen=True)

    max_tokens: int = Field(ge=1)
    temp_permille: int = Field(ge=0, le=2000)
    seed: int = Field(ge=0)


class GenResult(BaseModel):
    """What a backend returns. ``text`` is raw (the runner sanitizes:
    lone surrogates -> U+FFFD, NULs stripped) ; usage is true token counts
    (scripted backends synthesize ceil(len/4))."""

    model_config = ConfigDict(frozen=True)

    text: str
    usage_in: int = Field(ge=0)
    usage_out: int = Field(ge=0)


class ModelBackendP(Protocol):
    """A text generator. Implementations: MlxBackend (real, lazy import),
    ScriptedBackend (deterministic, CI), CachedBackend (deep replay —
    serves recorded LLM_CALL events in order, asserting prompt_sha)."""

    def generate(self, prompt: str, params: GenParams) -> GenResult: ...


# ------------------------------------------------------------- universe API


class TaskStub(BaseModel):
    model_config = ConfigDict(frozen=True)
    task_id: str
    tier: int = Field(ge=1, le=5)
    title: str  # e.g. 'produce "cinnabar-ash"'


class Submission(BaseModel):
    """An alchemy procedure: ordered pairwise combinations. Step i may use
    base elements and any product of steps < i."""

    model_config = ConfigDict(frozen=True)

    steps: list[list[str]]  # each inner list has exactly 2 entries (validated by the universe)


class Outcome(BaseModel):
    """Deterministic verification result. ``step_products`` is the evidence
    trail (what each combination yielded — informative even on failure;
    this is what makes experimentation learnable and teachable)."""

    model_config = ConfigDict(frozen=True)

    verified: bool
    product: str  # final product ("slag" on a dead-end combination)
    tier: int = Field(ge=0)  # tier of the attempted task
    step_products: list[str]
    message: str  # in-fiction, deterministic


class UniverseManifest(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    tiers: int = Field(ge=1)
    compounds: int = Field(ge=1)
    universe_seed: str  # hex


class AuditReport(BaseModel):
    """oracle_audit output: proves the world is worth simulating BEFORE a
    run burns a night. ok=False must fail `lamarck audit` loudly."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    reachable_tiers: list[int]
    compounds_by_tier: dict[str, int]  # str keys (canonical-JSON rule)
    min_steps: dict[str, int]  # compound -> minimal step count
    notes: list[str]


class CraftResult(BaseModel):
    """What one crafting attempt physically did — no commission semantics.

    The auto-claim rule (ratified 2026-08-06): the ENGINE maps produced
    compounds to board commissions via public titles and pays each
    first-per-cultivator claim; the universe only cooks. ``message`` is the
    deterministic in-fiction verdict naming only submitted ingredients and
    their products."""

    model_config = ConfigDict(frozen=True)

    step_products: list[str]
    message: str


class UniverseP(Protocol):
    """A problem domain with an instant, incorruptible, DETERMINISTIC
    verifier. Hidden rules must never appear in any prompt. Both methods are
    pure functions of their arguments — Phase 1 universes take no rng
    (deviation from PLAN §3.4, determinism-first; recorded in SPEC).
    ``available`` is the attempter's SATCHEL (2026-08-05): compounds the
    caller certifies the agent produced before this attempt; usable names
    are bases ∪ available ∪ earlier-step products of the same attempt.
    ``craft`` executes steps with no commission in sight (the auto-claim
    engine rule, 2026-08-06); ``attempt`` remains the task-scoped verifier
    used by audits and tests."""

    def manifest(self) -> UniverseManifest: ...
    def tasks(self, tier: int) -> list[TaskStub]: ...
    def craft(self, submission: Submission, available: frozenset[str]) -> CraftResult: ...
    def attempt(
        self, task_id: str, submission: Submission, available: frozenset[str]
    ) -> Outcome: ...
    def oracle_audit(self) -> AuditReport: ...


# ------------------------------------------------- perception and personas


class PersonaCard(BaseModel):
    """Immutable birth identity, loaded from personas/*.toml."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    name: str
    temperament: str
    values: list[str]
    quirks: list[str]
    speech_style: str


class HeardUtterance(BaseModel):
    model_config = ConfigDict(frozen=True)
    from_agent: str
    from_name: str
    text: str


class PerceptionView(BaseModel):
    """Everything a live agent may condition on at one tick — the
    determinism boundary. The prompt builder renders EXACTLY this (plus the
    versioned template) and nothing else; the runner assembles it purely
    from event-log-derived state."""

    model_config = ConfigDict(frozen=True)

    day: int
    round: int
    tick: int
    persona: PersonaCard
    location: str
    locations: list[str]
    qi: int
    stones: int
    allowance_left: int
    co_present: list[str]  # display names of living co-located others
    heard: list[HeardUtterance]  # <= 6, most recent last, day >= day-1
    notes: list[str]  # own last 5 NOTE texts, oldest first
    reflection: str  # own latest REFLECTION text ("" on day 0)
    outcomes: list[str]  # own last 3 TASK_ATTEMPT messages, oldest first
    satchel: list[str] = Field(default_factory=list)
    """Compounds this agent has ever produced (non-slag step products),
    first-acquired order, deduped — the personal tech tree. Usable as
    experiment ingredients alongside bases (satchel world rule, 2026-08-05).
    Default [] keeps Phase-0-era fixtures valid."""
    journal: list[tuple[str, str, str]] = Field(default_factory=list)
    """Lab journal (2026-08-19): every ingredient pair this agent has ever
    combined, with its product — ``(a, b, product)``, ingredients
    alphabetical within an entry, first-tried order, deduped by pair.
    Cumulative for life: the durable memory that lets an agent see which
    pairs are already spent. Default [] keeps older fixtures valid."""
    tasks: list[TaskStub]  # the visible task board
    materials: list[int]  # stones cost by tier (from config)
    bounties: list[int]  # payout by tier (from config)


# ------------------------------------------------------------- live config


class ModelSection(BaseModel):
    model_config = ConfigDict(frozen=True)
    backend: str  # "mlx" | "anthropic" | "scripted"
    model_id: str
    max_tokens: int = Field(ge=1)
    reflection_max_tokens: int = Field(ge=1)
    temp_permille: int = Field(ge=0, le=2000)
    seed: int = Field(ge=0)
    prompt_budget_chars: int = Field(ge=1000)
    wave_concurrency: int = Field(default=1, ge=1, le=16)
    """Transport-level concurrency for simultaneous rounds (2026-08-18):
    perceptions snapshot at round start, all lanes' model calls may fly
    concurrently, and results COMMIT in scheduler order — the event log and
    every replay stay byte-identical at any concurrency (tested). 1 =
    sequential (CI/scripted/mlx); cloud configs raise it to ~founders."""


class UniverseSection(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str  # "wuxing"
    seed: str  # hex; independent of the world master seed by design
    tiers: int = Field(ge=1, le=5)


class LiveSection(BaseModel):
    model_config = ConfigDict(frozen=True)
    locations: list[str] = Field(min_length=1)
    first_discovery_multiplier: int = Field(ge=1)


class LiveWorldConfig(WorldConfig):
    """The live world = the frozen Phase-0 config plus Phase-1 sections.
    Kept as a SEPARATE model so the Phase-0 stub `config_sha` (and with it
    the Phase-0 golden fingerprint) is untouched; live runs fingerprint the
    full extended model via `live_config_sha`."""

    model: ModelSection
    universe: UniverseSection
    live: LiveSection


__all__ = [
    "SCHEMA_VERSION",
    "GENESIS_HASH",
    "MASTER_SEED_DEFAULT",
    "WORLD_ACTOR",
    "EventKind",
    "DayPhase",
    "ActionType",
    "EventDraft",
    "EventRecord",
    "WorldSection",
    "PopulationSection",
    "QiSection",
    "EconomySection",
    "WorldConfig",
    "LedgerBalances",
    "EventStoreP",
    "RngStreamsP",
    "SchedulerP",
    "LedgersP",
    "StubPolicyP",
    "StubView",
    "TickSlot",
    "Iterable",
    # Phase 1
    "QI_INPUT_DIVISOR",
    "MAX_ACTION_PARSE_RETRIES",
    "NOTE_MAX_CHARS",
    "qi_llm_cost",
    "GenParams",
    "GenResult",
    "ModelBackendP",
    "TaskStub",
    "Submission",
    "Outcome",
    "UniverseManifest",
    "AuditReport",
    "CraftResult",
    "UniverseP",
    "PersonaCard",
    "HeardUtterance",
    "PerceptionView",
    "ModelSection",
    "UniverseSection",
    "LiveSection",
    "LiveWorldConfig",
]
