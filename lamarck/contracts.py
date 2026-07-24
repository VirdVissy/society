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
PHASE-0 ACTION SEMANTICS (stub world; replaced by real semantics in Phase 1)
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
]
