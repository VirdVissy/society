# devlog 002 — Phase 1: The Valley Awakens

*Status: DRAFT — measured numbers land at integration. Nothing here is pinned
until this line is removed.*

## What Phase 1 is

The valley gets minds: eight founder personas on a local Qwen3-4B (MLX),
thinking against a procedurally generated hidden chemistry (wuxing), with
every model call hash-chained into the Phase-0 event log. Qi billing moves
from flat action prices to real token usage (`ceil(in/4) + out`). Acceptance
(PLAN §4): 8 agents × 30 sim-days overnight unattended, zero unhandled
exceptions; ≥ 15 distinct verified discoveries incl. ≥ 1 tier-3; full-run
replay byte-identical from the response cache; pinned numbers below.

## How it was built

Four Fable subagents against the extended frozen contracts, two waves:

- Wave 1 (parallel ×3): wuxing universe + oracle audit; serving backends
  (Mlx lazy / Scripted for CI) + prompt builder + action-protocol parser +
  persona loader; engine extensions (LLM_CALL billing through ledgers +
  fold, world-state fold for locations/utterances/notes, live config).
- Wave 2: the live runner (cognition loop), deep replay, dashboard, report
  generator, scripted-backend golden.
- Integration: gates, real-model smoke, the 30-day acceptance run, this
  devlog.

Contract frictions ratified this phase (full list in SPEC §9a): world-slot
round numbering, consecutive per-agent ticks, NFC key-collision rejection,
degraded-REST billing, wuxing producibility cap (free generation produced
14–16-step recipes unwinnable under the 12-step cap; generation now
enforces closure ≤ MAX_STEPS − (tiers − tier)), trade-poverty routed to the
retry path, and the two mid-phase pivots below.

### Pivot 1 — prompt template p1.1 (the local smoke earned its keep)

The 2-day local smoke (Qwen3-4B) produced 48 experiment attempts and ZERO
discoveries. Diagnosis from the event log: agents emitted valid JSON whose
`steps` were narrative prose ("expose to moonlight and silence") or
fragmented compound names (`["silt", "ash"]` for silt-ash) — the protocol
never said steps are exact ingredient names. p1.1 states it, lists the five
bases on the task board, and adds one worked example. Golden re-pins were
mechanical; every behavioral pin (873 events, 81/8 discoveries, retry and
forfeit counts) survived, proving the scripted policy was wording-robust.

### Pivot 2 — cloud inference (user decision, laptop relief)

`AnthropicBackend` (Haiku 4.5) became the acceptance-run tier; mlx stays
first-class for dev and for Phase 3's weight training. Determinism was
never local-dependent: record/replay via the hash chain, verified below.

### Pivot 3 — once-per-cultivator bounties + board notes (paid-run evidence)

The first acceptance attempt (halted at day 5 by account credit exhaustion)
exposed **bounty farming**: unlimited repeat pay made re-verifying 2 known
commissions dominate exploration (20 payouts, 2 distinct, frontier dead).
Fix: the board honors each commission once per cultivator, enforced by the
runner AND the shallow-replay verifier, stated on the board (p1.2).

The second attempt (halted deliberately at day ~14) exposed the successor
failure: with farming unpaid, agents had produced **all 8 tier-1
compounds** (90 productions inside unverified attempts — autumn-mirror 24
times) yet filed only 2 commissions; income collapsed and days 8–13
drifted to resting/chatting (172 rest, 165 converse, 56 experiment). The
missing move was joining two public facts: "I made X" ↔ "commission N pays
for X". Fix: **board notes** — attempt messages now append the
cross-reference, recomputed identically by the verifier via a shared
helper; the reflection prompt nudges unclaimed-commission naming (p1.3).
Both halted runs were also unplanned stability tests: 19 combined
unattended sim-days, zero unhandled sim exceptions (the two halts were
external: billing, and my kill).

### Pivot 4 — resume + patience, then the inventory fallacy

The final acceptance run died at day 12 to a sustained API 529 window.
Two permanent pieces of infrastructure came out of it: an outer patience
loop in the cloud backend (transient classes only; ~12 min tolerance;
permanent 4xx still fail fast) and **`lamarck resume`** — day-batch
transactions guarantee a clean boundary, the engine refolds from the log,
and both RNG streams replay their recorded consumption exactly; the
reconstruction prover is deep replay of an interrupted-then-resumed run
reproducing the head byte-for-byte. The run resumed from day 13 with all
paid state intact — resume's first production use.

The resumed run then surfaced world-design finding #4: **the inventory
fallacy**. Agents mastered tier-1 (172 attempts after resume) but made
only two tier-2 attempts, both using a discovered product as a step-1
ingredient — they model products as stored inventory; the world requires
re-derivation within each attempt, and the only worked example was
single-step. Template p1.5 teaches the chain ("THE CRUCIBLE EMPTIES
BETWEEN ATTEMPTS" + a two-step worked example). Runner-side validation of
ingredient availability was considered and rejected on principle: step
N's availability depends on step N−1's hidden product — the universe
stays the only judge of chemistry. Halted at day ~20 rather than pay for
an unreachable gate; the canary protocol gained a chaining gate (≥1
verified tier-2 by day 4) before any full launch.

## Measured numbers

- **Local baseline** (mlx, Qwen3-4B-4bit, M3 Pro, template p1.0): 2-day
  smoke = 21.3 min wall (~10.6 min/sim-day → a 30-day run ≈ 5.3 h);
  deep replay byte-identical (head `379/9badd0b4c9f2`); 48 attempts,
  0 verified — the p1.1 finding above.
- **Cloud smoke** (Haiku 4.5, template p1.1): 2 days in 14.3 min
  (~7.1 min/sim-day); 484 events; 256 model calls (112 billed retries,
  93 recovered — parse-prose and trade-poverty dominated, both
  self-correcting); usage 784,391 in / 42,216 out ≈ **$0.99**;
  deep replay byte-identical (head `483/d7d7fae1648a`).
- **Protocol learnability** (the finding that green-lit the acceptance
  run): day 0 = 18 attempts / 0 verified (all agents citing the example's
  commission id); day 1 = 36 attempts / **10 verified** across
  diversifying task ids — agents connected "the crucible yields X" to the
  commission that pays for X without any prompt change.
- Qi calibration (cloud smoke): ~3,200 qi/agent/day median thinking cost
  against the 30k daily allowance — natural lifespan ≈ 370 sim-days at
  Phase-1 activity; lifespan pressure will need tuning when Phase 2 makes
  death matter. *(initial, one smoke's evidence)*
- **Acceptance run (30 days × 8, Haiku 4.5): (TO FILL on completion)** —
  wall, events, db size, distinct discoveries (incl. tier-3+), token
  totals and cost, degraded/malformed rates, deep-replay verdict.
- Suite: 483 tests + 1 local-only mlx skip, ~8 s. CI matrix: pending the
  repo's first push (unchanged since Phase 0).

## Deviations from PLAN (ratified this phase)

- Notes retrieval is recency-based (no embedding model): determinism-first;
  embeddings would be the only float-producing, second-model dependency in
  the loop. Revisit behind the response-cache pattern if recall proves weak.
- `Universe.attempt` takes no rng (PLAN §3.4 showed one): wuxing v1 is
  noise-free so learning signal and replay are exact.
- TRADE is a co-located stones transfer (gift/payment primitive), not a
  matching market: the negotiation-free core the Phase-2 teaching economy
  needs.
- TEACH/STUDY/CHALLENGE/ATTEMPT_BREAKTHROUGH are billed flavor no-ops until
  their phases (2/4) arrive.

## What Phase 1 deliberately does not do

No techniques/teaching semantics (Phase 2), no provenance store (Phase 2),
no weight training (Phase 3), no realms/tournaments (Phase 4). The point:
minds in the loop with the same reproducibility guarantees the stub world
had — an overnight society run you can replay byte-for-byte.

## Next (Phase 2 — Lineages)

Mind-git provenance (belief commits at dusk), the teaching protocol with
fidelity, death/succession, sects v1, literacy toggle, metrics suite v1,
and E2 (oral vs. archive).
