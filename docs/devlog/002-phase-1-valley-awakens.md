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

Contract frictions ratified this phase: (TO FILL at integration).

## Measured numbers (TO FILL)

- Model: … load time … s; generation … tok/s; prompt prefill … tok/s
- Smoke run (2 days × 8 agents): … min wall; … LLM calls; … events
- Qi calibration: median qi/agent/day … (allowance 30k; projected natural
  lifespan … sim-days)
- Acceptance run (30 days × 8): … h wall; … events; … MB db; discoveries
  … distinct (… tier-3+); degraded/malformed rates …/…
- Deep replay of the acceptance run: byte-identical head … ; … s
- Suite: … tests, … s; CI matrix green: …

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
