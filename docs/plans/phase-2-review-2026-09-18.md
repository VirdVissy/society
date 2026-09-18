# Phase 2 plan review — 64-point ledger (2026-09-18)

*Source: the 2026-09-07 multi-lens critique of plan v1 (six reviewers, each
adversarially re-checked against the code and `runs/phase1-accept2`), agreed
in full by the owner on 2026-09-18. Every point is listed in the order it
was presented, with its disposition and where it landed. "Plan" refers to
`docs/plans/phase-2-implementation.md` v2 (§ and D numbers); "Stage 0"
refers to the tools in `lamarck/analysis/` built in the same change set.*

| # | Point | Disposition | Where |
|---|---|---|---|
| 1 | Agents already teach in plain speech (~45% of utterances state a complete true recipe; zero TEACH). | Adopted as design evidence; measured by the exposure tool. | Plan §2.2; Stage 0 `exposure.py` R2–R3 |
| 2 | Hearing a recipe raises first-try within two days 2–5× over untried makeable pairs. | Measured with the control rate: 29% vs 6% (4.7×) under the frozen rule, 45% vs 6% at tier 1. | Plan §2.2; Stage 0 `exposure.py` R4 |
| 3 | TEACH-anchored provenance makes real transmission invisible. | Replaced by exposure-derived `taught`. | Plan D1 |
| 4 | `taught` fires on any later claim with no exposure check; ~half of second-holder acquisitions were independent. | `taught` requires a qualifying exposure within a window; acquisition classes reported. | Plan D1; Stage 0 R5 |
| 5 | Under the v1 schema `blame` can never reach an utterance. | `derives_from(Possession → Utterance)` from `taught.utterance_seq`. | Plan D2 |
| 6 | "Origin utterance" undefined for crucible-born techniques. | Root kinds Utterance \| Outcome; gate restated. | Plan D2, Stage F gate |
| 7 | Fix 1–6 with a strict deterministic matcher, speaker-holds rule, window, TEACH as upgrade. | Adopted verbatim as D1. | Plan D1 |
| 8 | Build and pin the detector for $0 against the Phase-1 log first. | Stage 0 built before any runner code. | Plan Stage 0; `lamarck/analysis/exposure.py` + tests |
| 9 | Uniform spend: all founders die within a two-day window at any `qi_max`. | Reproduced by the simulator; drives stagger. | Plan §2.4; Stage 0 `lifespan.py` |
| 10 | Next-dawn full-qi heirs make synchronized cohorts with 1–2 day overlap. | Stagger persists through heir birth days; overlap is a bar. | Plan D3, Stage E2 bar |
| 11 | The 12-day generational canary at 250k cannot meet its bar. | Canary redesigned: 14 days at production `qi_max`; bars from the simulator. | Plan Stage E2 |
| 12 | The 6-day mortality canary at 90k has no margin. | Smoke rung is 8 days at ≈70k with deaths required by day 6. | Plan Stage E1 |
| 13 | Termination ends the run before dawn succession. | "Continue while a succession is pending." | Plan D3; B1 deliverable |
| 14 | Options: qi jitter, staggered spawn, apprentice overlap, pending-succession rule. | Chose jitter from the `"lifespan"` stream (floor 600‰) + pending rule; spawn-day stagger and apprentice model rejected (persona-correlated / changes constant population). | Plan D3 |
| 15 | $0 schedule simulation must set every canary bar. | `lifespan.py` + `canary_check`; every bar in Stage E is computed. | Stage 0; Plan Stage E |
| 16 | v1's "ceiling ≈19 days" was an arithmetic error. | Corrected to 8.3–10.5 days at 200k. | Plan §2.3 |
| 17 | Most actions cost two calls (truncation); a large share of thinking qi goes to truncated first calls. | Measured by the retread tool: 94% of actions retried, 47% of thinking qi on truncated first calls, reflections truncated 97%; the review's 85%/40% figures were a conflation, corrected in plan §2.3; drives D10. | Plan §2.3; Stage 0 `retread.py` truncation |
| 18 | Raising `max_tokens` halves spend; retune after template + `max_tokens`. | Retune procedure: choose `max_tokens` at freeze, measure in E1, then set `qi_max` by simulator. | Plan D10 |
| 19 | No thrift lever; prompt length sets lifespan; archive arm pays more. | Covariates (qi/day, `usage_in`, lifespan) and ±5% `usage_in` parity check pre-registered; stele hard cap. | Plan §2.3, D5, D12 |
| 20 | Archive exists only inside a sect that needs a social action. | Stele exists without any action; sects removed. | Plan D5 |
| 21 | No sect ⇒ archive arm equals oral arm; no manipulation check. | Manipulation checks pre-registered; E1/E2 run an archive arm. | Plan D5, Stage E |
| 22 | Archive specified three ways. | One construct: the dead agent's journal persists to the stele. | Plan D5 |
| 23 | Notes are a lossy, persona-confounded substrate. | Archive is the engine journal, not note text. | Plan D5 |
| 24 | Archive block would cut speech first under budget pressure. | Stele is floor content with a hard cap; speech untouched by it. | Plan D5 |
| 25 | Archive characters shorten archive-arm lives (confound in the predicted direction). | Cap + parity check + covariates. | Plan D5, D12 |
| 26 | Neither canary rung exercises the archive path. | E1 and E2 each run one archive arm with a stele-rendered bar. | Plan Stage E |
| 27 | Proposed fix for 20–26. | Adopted. | Plan D5 |
| 28 | One location, zero travel ⇒ one sect containing everyone. | Sects removed from Phase 2. | Plan D5, §10 #5 |
| 29 | Travel-cost raise reprices an action nobody takes. | Removed. | Plan D5 |
| 30 | Defer sects and the travel raise to Phase 4, ratified. | Deviation ledger row. | Plan §10 #5, §9 |
| 31 | Edit-distance fidelity ≡ 1 in wuxing. | Dropped; canonical technique is the closure. | Plan §2.1, D2, D9 |
| 32 | Mutation trees/rate ≡ ∅. | Dropped; `phylo`/`epidemiology` out of Phase 2. | Plan D8, D9 |
| 33 | KM over holders ≡ lifespan. | Replaced by technique-level extinction and frontier recovery. | Plan D9 |
| 34 | Ratchet undefined for pair recipes and dragged by tier-1 re-claims. | Ratchet = living-knowledge AUC + max living tier. | Plan D9 |
| 35 | Real mutation channel: false recipe assertions replicate. | Engine-extracted assertions; false-claim metrics. | Plan §2.7, D7, D9; Stage 0 R6 |
| 36 | Better primaries (living knowledge, extinction/recovery, transmission coverage, false claims). | Locked as D9. | Plan D9 |
| 37 | Two master seeds pair nothing. | Stated: n=2 unpaired replicates on one universe. | Plan D12 |
| 38 | Pre-registration has names only. | Full registration contents specified (formulas, signs, SESOI, decision rule, floors). | Plan D12, Stage C |
| 39 | "Generation" undefined. | Lineage depth; run reaches g when ≥⌈n/2⌉ lineages have a living member at depth ≥ g. | Plan D9 |
| 40 | Optional stopping and substitution asymmetries. | Fixed arm length per pair; F1 never substituted. | Plan D12, Stage F |
| 41 | `belief_commit` has no producer and cites unseen seqs. | Deferred to Phase 3. | Plan D7, §10 #4 |
| 42 | Honest options: second dusk call vs engine-derived vs defer. | Deferred; assertions engine-extracted in analysis only. | Plan D7 |
| 43 | Succession specified three ways. | One: deterministic lineage heir (same card, roman suffix, `<root>g<n>` id). | Plan D4 |
| 44 | Transcript-generated heir breaks symmetry and replay. | Rejected. | Plan D4 |
| 45 | RNG-drawn heir diverges personas between arms. | Rejected; heir is a pure function of lineage. | Plan D4 |
| 46 | Inheritance vector unspecified; notes inheritance in oral mode. | Pinned: stones reset, location inherited, everything else empty; no notes. | Plan D4 |
| 47 | Pool sizing (≥24 cards) vs deterministic heir. | Deterministic heir needs no cards. | Plan D4 |
| 48 | Runner assumes a fixed population everywhere. | `register_agent` helper; succession-before-`iter_day` layout; resume rebuild for mid-log spawns. | Plan D4; B1 |
| 49 | Deathbed line cannot live in the pinned system prompt; needs a pure-fold trigger. | User render; `qi ≤ window × daily_allowance`. | Plan D3 |
| 50 | Any new `ActionType` makes every Phase-1 run unreplayable. | Ground rule: no new `ActionType`; sects removed; archival fixture + Phase-1 replay test. | Plan §1, D6, D11 |
| 51 | TEACH/STUDY args are mandatory by construction; v1's fallback was the design. | TEACH `{target, student}` mandatory; STUDY unchanged; fallback removed. | Plan D6 |
| 52 | Provenance index cannot live in the run's events database. | Sidecar `provenance.sqlite3`; canonical dump sha. | Plan D8, §10 #12 |
| 53 | All briefs built before any paid evidence. | Ladder reordered: Stage 0 tools → minimal freeze → build → smoke rung. | Plan §4, Stage 0, Stage E1 |
| 54 | Reorder: retro-provenance, minimal Stage A, teaching smoke, then the store. | Adopted; the smoke doubles as the mortality rung. | Plan §4 |
| 55 | Run one 30-day archive arm as the gate candidate before the other three. | F1 before F2. | Plan Stage F |
| 56 | E2 costs $68–82, not $40–55; Stage E heading said $6. | Budget rebuilt from $0.68 per arm-day, +15% for p3.0. | Plan §6 |
| 57 | Re-canary reserve unbudgeted. | Reserve line (≈$21). | Plan §6 |
| 58 | Realistic exposure $110–140; prompt caching cannot help. | Stated ($125 expected, $145 ceiling); caching noted as inapplicable. | Plan §6 |
| 59 | Canary "crosses a death" bar names no tier or novelty condition. | Bar: cross-generation `taught`, tier ≥ 2, student naive. | Plan Stage E2 |
| 60 | Retread not a metric; baseline is a distinct count; bar contradicts the risk table. | Pair-level own-journal retread per life-day, baseline from the accepted run; only own-journal retread is barred. | Plan D9, Stage E2, §5; Stage 0 `retread.py` |
| 61 | 1,161 of 1,210 retread steps re-tried own known slag. | Measured and pinned; devlog 002 erratum. | Plan §2.6; devlog 002 errata; Stage 0 |
| 62 | Contradicts "frontier saturation, not amnesia"; world-rule candidate. | Erratum written; world rule recorded for a later phase (would confound E2). | devlog 002 errata; Plan §9 |
| 63 | Phase-3 hand-off under-secured (`llm_texts`, `llm_seq`, Haiku logs). | Run-end `llm_texts` check, retention ratified, `llm_seq` on nodes, Stage G note. | Plan D2, D11, Stage G, §10 #16 |
| 64 | Scope: write the cut order now rather than cut. | Cut order written; kill line dated. | Plan §7, §8 |
