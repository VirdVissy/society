# devlog 001 — Phase 0: Foundations

*Status: DRAFT — measured numbers land at integration. Nothing here is pinned
until this line is removed.*

## What Phase 0 is

A deterministic, empty world that can never lie to us: event-sourced state,
hash-chained log, independent ledger difftest, seeded RNG substreams, a
scripted StubAgent exercising every action type, and a golden replay test —
zero LLM involvement. Acceptance (PLAN §4): 1,000-day stub sim < 5 s; replay
hash-chain identical across runs; difftest green; CI green.

## How it was built

Phase 0 was built by three Fable subagents against a frozen contract file
(`lamarck/contracts.py`), in two waves:

- Wave 1 (parallel): eventstore (canonical JSON + hash chain + SQLite WAL
  store) and engine (config, RNG streams, scheduler, ledgers + difftest fold),
  with disjoint file ownership.
- Wave 2: StubAgent + sim runner + CLI + golden replay + perf gate, on top of
  the integrated Wave-1 seam.
- Integration, SPEC extraction, and this devlog by the orchestrating session.

The coordination lesson worth keeping: freezing the interface contract *and
its ambiguities* (canonical-JSON rules, hash-chain byte layout, RNG
derivation formula, stub action semantics) before spawning parallel builders
is what makes same-repo parallelism safe. Contract frictions reported by the
builders: (TO FILL at integration).

## Measured numbers (TO FILL)

- 1,000-day / 8-agent stub sim: … s wall (gate: < 5 s), … events, … MB db
- Batched append rate: … events/s
- Replay verification of the same run: … s; chain head identical: …
- Test suite: … tests, … s
- Golden fingerprint (config `configs/world.toml`, seed `0xDE5EEDDE5EEDDE5E`,
  pinned in `tests/test_sim_golden.py`): `…`

## Deviations and decisions

See SPEC §9. (Expand at integration if the waves surfaced more.)

## What Phase 0 deliberately does not do

No LLM calls, no personas, no universes, no provenance store, no economy
beyond bounties/materials, no viewer. The point was the substrate: if the
event log, hashes, RNG, and ledgers are trustworthy now, every later phase
inherits reproducibility for free.

## Next (Phase 1 — The Valley Awakens)

mlx-lm serving + response cache wired into this event log; cognition loop;
wuxing universe v1 + oracle audit; 8 founder personas; first overnight run
with real discoveries. Gate: PLAN §4 Phase 1 acceptance.
