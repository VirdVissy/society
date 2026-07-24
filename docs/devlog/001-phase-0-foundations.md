# devlog 001 — Phase 0: Foundations (v0.1.0)

*2026-07-23. Phase 0 CLOSED (local); CI verification pending first push to a
remote.*

## What Phase 0 is

A deterministic, empty world that can never lie to us: event-sourced state,
hash-chained log, independent ledger difftest, seeded RNG substreams, a
scripted StubAgent exercising every action type, and a model-free replay
verifier — zero LLM involvement. Acceptance (PLAN §4): 1,000-day stub sim
< 5 s; replay hash-chain identical across runs; difftest green; CI green.

## Measured numbers (integration runs, M3 Pro, warm)

- **1,000-day / 8-agent sim: 2.103 s** (gate < 5 s) — 71,366 events,
  ~33,900 events/s end-to-end (policy + scheduler + ledgers + hashing +
  SQLite included). All 8 agents alive at day 1000 (perf config raises
  qi_max to 10 M so the full span is exercised).
- **Golden run** (verbatim `configs/world.toml`, 30 days, 8 agents):
  run_id `903276a6edd8`, 2,161 events, chain head
  `2160/a362ae0d455b18877b70f7ce7af8f5c95c2527d2c1308974c4efaaa695ab2681`,
  final_state_sha `f990988a…25a4aa4a`, 81 discoveries, 101 degraded
  actions, 75 ms wall. Pins held across four separate processes including
  randomized `PYTHONHASHSEED`; CLI `run` then `replay` reproduces and
  verifies the same fingerprint (exit 0).
- **Eventstore micro-benchmarks** (builder-measured, 100k events):
  ~87k appends/s batched, verify_chain ~106k ev/s, scan ~345k ev/s.
- **Suite: 146 tests** (5 contract locks, 66 eventstore, 60 engine, 15
  sim/CLI/perf), ~3.4 s. ruff + ruff-format + mypy (strict on
  eventstore/engine/contracts) all clean.
- Golden-run behavior coverage: all 11 ActionTypes executed (experiment 278,
  rest 294, converse 234, study 207, meditate 203, trade 154, teach 141,
  note 125, travel 108, attempt_breakthrough 90, challenge 86); degradation
  exercised via stone-poverty (101 events); the allowance-exhaustion and
  free-rest branches don't bind under the canonical config and are locked by
  a dedicated tiny-allowance edge test (145 free + 42 paid degraded events);
  death + early-termination + tamper-detection covered by edge tests.

## How it was built

Three Fable subagents against a frozen contract file (`lamarck/contracts.py`),
in two waves with disjoint file ownership:

- **Wave 1 (parallel):** eventstore (canonical JSON, hash chain, SQLite WAL
  store, tamper-detecting verify_chain) and engine (config loader + config
  sha, splitmix64 substreams, day scheduler, live ledgers + independent fold
  + allowance meter).
- **Wave 2:** StubAgent, sim runner (event layout pinned in `sim.py`'s
  docstring), typer CLI, golden/edge/perf/CLI tests.
- Integration, SPEC extraction, and this devlog by the orchestrating session,
  which re-ran every gate and the acceptance evidence independently.

Coordination lessons worth keeping:

1. **Freeze the contract, including its ambiguities, before spawning
   builders.** Zero contract violations and zero file-ownership collisions
   across three agents; the one seam that had to land mid-flight
   (`config_sha` importing the concurrently-built canonical encoder) was
   pre-declared with a skip-guard and wired itself live without integrator
   action.
2. **Require frictions to be reported, not resolved.** All three builders hit
   unpinned edge cases (world-slot round numbering, consecutive-vs-interleaved
   ticks, NFC key collisions, IntEnum handling, degraded-REST billing when
   even REST is unaffordable). Every one came back as a documented decision
   to ratify rather than a silent divergence; the ratified set is now locked
   in SPEC §§1–8.
3. **Reference-anchor before pinning.** The splitmix64 vectors were checked
   against the published seed-0 sequence before being frozen, so the pins
   prove correctness rather than merely freezing whatever the code did. Same
   philosophy as loom's cross-checked encoders.

## Deviations from PLAN §3 (ratified)

`stones_delta` added to the event envelope; PLAN §9's top-level dirs live
under the `lamarck/` package; probabilities are permille integers;
difftest cadence is periodic + at-run-end rather than every dusk (a
full-rescan fold every dusk would be O(n²) over a long run). Known accepted
limit: the hash chain proves integrity/order of present events, not absence
of tail truncation — external head evidence (report.json, golden pins) is
the truncation witness.

## What Phase 0 deliberately does not do

No LLM calls, no personas, no universes, no provenance store, no economy
beyond bounties/materials, no viewer. The point was the substrate: the event
log, hashes, RNG, and ledgers are now trustworthy, so every later phase
inherits reproducibility for free.

## Next (Phase 1 — The Valley Awakens)

mlx-lm serving + response cache wired into this event log (the LLM-call
record kind slots into the same chain); cognition loop + strict action
protocol; wuxing universe v1 + oracle audit; 8 founder personas; notes
memory; first overnight run with real discoveries. Gate: PLAN §4 Phase 1
acceptance. Immediate housekeeping: create the GitHub remote, push, confirm
CI green on both runners, then push tag v0.1.0 (tag exists locally).
