# Phase 2 — Lineages: implementation plan (v2, 2026-09-18)

*Target: `v0.3.0`. Constitution: PLAN.md §4 "Phase 2 — Lineages" and §3.5–3.8,
§3.12. This is the second version of the plan. v1 (2026-08-22, amended
2026-09-06) was reviewed point by point on 2026-09-07 against the accepted
Phase-1 run's event log; the 64 findings and where each one landed are in
`docs/plans/phase-2-review-2026-09-18.md`. Nothing in this file overrides
PLAN.md; every deviation forced by evidence is listed in §10 and is ratified
into SPEC §9b at the contract freeze.*

---

## 0. What Phase 2 is, in one paragraph

Phase 1 proved individual discovery: eight immortal-in-practice agents, each
alone with a task board, found 24/34 compounds — and, unprompted, told each
other the recipes (30% of all utterances name an ingredient pair the speaker
had already made; 15% state the complete recipe; 21 of 24 recipes were spoken
in full). Phase
2 makes mortality real and measures the culture that already exists: agents
die on a staggered schedule, are replaced by heirs of their own line, and
the engine attributes every technique in the population to the utterance
that carried it or the crucible that first produced it. The phase ends with
E2 — does a written record that outlives its author (journal inheritance)
change what the population keeps? — as the project's first pre-registered
science. The gate is generational: three-plus generations in one unattended
run with `blame` resolving every technique to a root, byte-identical replay.

## 1. Ground rules (non-negotiable)

- **Event-sourced everything.** New state (exposure, generations, the
  archive) is a pure fold of the hash-chained log. The provenance graph is a
  derived sidecar index, rebuildable from scratch at any seq.
- **Shared-helper symmetry.** Every rule the runner applies (exposure match,
  `taught` derivation, succession, archive fold) is enforced by the
  shallow-replay verifier through the *same imported helper*.
- **World-not-prompt.** When agents systematically fail, change the world to
  match the intuition every model already has; prompt text states rules, it
  never carries load-bearing memory. Phase 2 adds no optional social action
  that the manipulation depends on (Phase-1 evidence: zero travel, teach,
  study or trade actions in 1,920).
- **Template versioning.** p2.3 → p3.0 at the cutover; every wording change
  re-pins the mind and live goldens in the same commit and pins the input
  token delta (the journal cost +6%).
- **Determinism.** New RNG consumers get named streams replayed by resume;
  no new wave type in Phase 2 (no second dusk call); deep replay stays
  byte-identical for logs of the same template, and shallow replay stays
  forever (see D11).
- **No new `ActionType`.** Config loading requires the action-cost table to
  match the enum exactly, and replay loads a run's own config first; a new
  verb would make every Phase-1 log unreplayable. TEACH gains arguments;
  nothing else changes shape.
- **Analysis first.** The attribution rule is prototyped and pinned on the
  Phase-1 log before any runner code depends on it (Stage 0).
- **Paid-run protocol.** Scripted backend for all development ($0); every
  paid rung has a pre-registered bar computed by the schedule simulator; paid
  launches only on the user's explicit go with the run's cost stated;
  Monitor with failure signatures; deep replay before any success claim.

## 2. Phase-1 evidence that shapes the design

All numbers are from the accepted run `aea04d1a8e6f` (`runs/phase1-accept2`)
unless stated; the tools in `lamarck/analysis/` recompute them.

1. **Techniques are recipes, and every recipe is unique.** Each compound has
   exactly one unordered ingredient pair, so the derivation from bases is
   unique per compound. Consequences: the canonical technique is the
   satchel-free closure (D2); edit-distance fidelity is identically 1 and
   route mutation is impossible — both are dropped as metrics (D9).
2. **Transmission already happens in speech, with no protocol.** Under the
   frozen D1 rule (`lamarck exposure`, connector-joined pairs, window 2):
   169 of 567 utterances name an ingredient pair the speaker had already
   produced (84 also name the product; 257/111 under the looser
   any-two-names-in-a-sentence reading); 21 of 24 recipes were spoken in
   full; zero TEACH actions. Those utterances produced 1,729 exposures, 462
   of them to a listener who had never tried the pair; 136 of those 462
   (29%) were first-tried within two days, against 6% for untried makeable
   pairs nobody mentioned — 4.7×; 45% at tier 1 against the pooled 6%
   control. Of the 105
   non-first acquisitions, 51 followed a qualifying exposure (lag 0/1/2
   days: 8/23/20) and 54 were independent; the leading transmitters were
   Mei Lin (15), Fen Tu (13) and Han Yue (12). Attribution must therefore be
   engine-derived from delivered utterances (D1); a declared TEACH is an
   intent marker, not the anchor.
3. **Lifespan is set by spend that is nearly uniform and dominated by
   truncation.** Every agent uses every slot; per-agent spend is 19.0k–24.2k
   qi/day (all agent-days lower median 21,716 as `lamarck lifespan` reports;
   96% thinking; 87% of thinking qi is input-side). 1,803 of 1,920 actions
   (94%) cost two model calls — 1,801 of them because the first reply hit
   `max_tokens = 220` and was retried; 47% of thinking qi went to those
   truncated first calls, and dusk reflections were truncated 233 times out
   of 240 at 160 tokens. (The 2026-09-07 review quoted 85% and 40% for these
   two; the tool corrects them.) There is no thrift lever; the most prolific
   experimenter dies first; larger prompts shorten lives. So: at `qi_max`
   200k the simulator has every founder dying at the dusk of days 9–11, i.e.
   10–12 days alive (8.3–10.5 by mean spend alone; the v1 "ceiling ≈19 days"
   was an arithmetic error), `qi_max` is retuned only after the template and
   `max_tokens` decision (D10), and per-arm qi/day is an E2 covariate.
4. **Uniform spend makes a population cliff.** Simulating each founder's
   real spend: all eight die across three consecutive days (spread 2) at any
   `qi_max` (200k: days 9–11; 250k: 11–13; 90k: 4–5). With next-dawn
   replacement, generations
   are synchronized cohorts and teacher–heir overlap is 1–2 days. Founders
   must be staggered (D3). `lamarck lifespan` reproduces this exactly and
   shows the stagger working: with starting qi drawn uniformly in
   600–1000‰ (seed 1), first deaths at 200k spread over days 6–11 instead of
   9–11, heirs overlap a living founder for 24 agent-days instead of 9, and
   half the lineages reach generation 3 by day 20 instead of 22.
5. **Optional actions are never taken; new blocks compete for a budget that
   is already near its ceiling.** Travel 0, teach 0, study 0, trade 0 in
   1,920 actions; notes 88. Median user prompt 11.7k of the 14,000-char
   budget; the drop order removes speech first. The E2 manipulation must
   exist without any agent action, be rendered at a fixed size, and be
   costed (D5).
6. **The journal's dead-ends line is rendered but not read.** Of 1,210
   retread steps, 1,161 re-tried a pair the actor's own journal already
   listed as slag (one agent retried one dead pair 35 times; 508 of 872
   non-empty attempts contained no novel pair). Devlog 002's
   "frontier saturation, not amnesia" reading is corrected in its errata;
   own-journal slag retread per life-day is a locked metric (D9) and a
   world-rule candidate is recorded in §9.
7. **Recipe misinformation exists and replicates.** Of 191 declarative
   recipe assertions in speech 15 are false (8%), of 64 in notes 11 (17%);
   6 false assertions were repeated by another agent within three days. On
   day 27 Yan Hua asserted a recipe for a pair whose only attempt had given
   slag; Han Yue repeated it twice on day 28, and Qing Zhao and Bo Shan
   burned six experiments on it. This is
   the one cultural-mutation phenomenon the universe exhibits; it is measured
   from engine-extracted assertions (D7, D9), never from model-cited claims.
8. **Possession is already proven per agent** by the once-per-cultivator
   auto-claim ledger; teaching grants nothing by fiat and ends in the
   student's own verified production. **Waves** make speech land next round,
   so the protocol is asynchronous by construction.

## 3. Design decisions (inputs to the freeze)

### D1 — Exposure and `taught` (the attribution rule)

Names: the five bases match word-bounded, case-insensitive; compound names
match as whole hyphenated tokens, case-insensitive (the universe guarantees
no substring collisions). Sentences split on `. ! ? ; :` and newlines. A
sentence **names pair `(a,b)`** iff `a` and `b` are two consecutive
ingredient mentions whose gap consists only of pair connectors (`+ - / &`,
"and", "with", "plus", "paired with", "together with", "against", with
optional whitespace around them — bare whitespace alone is not a
connector), taken left to right without overlap — so "fire-metal",
"fire + metal" and "fire and metal" all name (fire, metal) while "fire,
then earth" and "fire metal" name nothing. (The looser "any two names in one sentence" reading is measured
alongside, never used for attribution; it counts enumeration cross-pairs
the speaker never joined. Known limitation to revisit at the freeze: an
unpunctuated "… made X and earth and water made Y" pairs X with earth.)

- **Exposure** `(listener L, recipe R = (a,b → t), utterance seq s)` exists
  iff a non-degraded CONVERSE at `s` by speaker `S ≠ L` has a sentence
  naming pair `(a,b)`; `S`'s journal at `s` holds `(a,b) → t` with
  `t ≠ slag`; and the world fold delivered the utterance to `L` (co-located,
  alive, at emission — SPEC §9a). `product_named` records whether `t` is in
  the same sentence. Exposure is fold state (like the heard inbox), not a
  chain event, and it is computed only from hashed payload text. The rule
  text is the docstring of `lamarck/analysis/exposure.py` (R2–R3) and its
  Phase-1 numbers are pinned in `tests/test_analysis_exposure.py`.
- **`taught`** (world event, committed by the runner, re-derived by the
  verifier via one helper `_derive_taught`, which imports the Stage-0
  predicate `classify_acquisitions` rather than restating it) fires when
  `L`'s FIRST claim of `t` lands and there exists an exposure `(L, R, s)`
  with `s < claim_seq`, `L` naive at `s` (the pair absent from `L`'s journal
  before `s`), and `claim_day − day(s) ≤ lineage.teach_window_days`.
  Window: initial 3 — a margin choice, not a median: measured lags at window
  2 are 0/1/2 days = 8/23/20 (median 1); window 3 adds 2 transmissions (53
  of 105 non-first acquisitions). Payload: `{teacher: S of the earliest
  qualifying exposure, student: L, target: t, tier, utterance_seq: s,
  claim_seq, declared: bool, exposures: [seq…]}`. `declared` is true iff
  `S` committed `TEACH {target: t, student: L}` before the claim.
- **Stele exposure** (archive arm): `(L, R, seq)` exists iff a stele entry
  `(a,b → t)` written by a dead author is inside the capped block rendered
  to `L` at `L`'s action `seq` (a pure function of the fold: `L`'s location,
  the stele contents and `stele_render_chars`). A first claim with a
  qualifying stele exposure and no qualifying utterance exposure is class
  `archival`; its provenance edge is `derives_from(Possession → Death)` of
  the author, and `blame` continues through the author's own chain. Without
  this the E2 mechanism would be invisible to every attribution metric.
- **Acquisition classes**, reported for every `(agent, target)` first
  production: `first_in_world`, `transmitted` (a `taught` fired),
  `archival` (stele exposure, archive arm only), `independent`. Phase-1
  baseline at window 2 (per tier 1/2/3/4/5):
  first-in-world 8/8/6/1/1, transmitted 25/19/7/0/0, independent
  30/17/7/0/0; at window 3 transmitted rises to 53 of 105.
- **Not a rule**: whether the utterance was actually rendered in a prompt
  (the 6-utterance cap and budget drops). That is an analysis flag from
  `llm_texts`, reported as a manipulation check, never part of the chain.

### D2 — Techniques, provenance graph, and the gate

- Canonical technique for target `t` = the full derivation closure from
  bases, ingredients sorted within a pair, steps in post-order with siblings
  sorted by product name; unique per compound, satchel-free, hash-stable.
- Provenance graph (derived sidecar, D8): node kinds `Utterance` (CONVERSE
  seq), `Outcome` (TASK_ATTEMPT seq), `Possession` (agent × target, created
  at the paying claim), `Birth` (AGENT_SPAWNED), `Death` (AGENT_DIED); every
  event-backed node carries `llm_seq` of its producing LLM_CALL. Edge kinds
  `verified_by` (Possession → Outcome), `taught_by` (Possession →
  teacher's Possession), `derives_from` (Possession → Utterance, from
  `taught.utterance_seq`), `child_of` (heir Birth → predecessor Death).
- **`blame(agent, target)`** walks `taught_by`/`derives_from` from the
  possession to a root. Root kinds: `Utterance` (transmitted), `Death` of a
  stele author (archival; the walk may continue through the author's own
  chain), or `Outcome` (first-in-world or independent production). **Gate
  clause restated**: for every Possession in the run, `blame` resolves to
  exactly one root with no dangling edge, and the chain re-derives from the
  log; the utterance-rooted share is reported (Phase-1 baseline: 51 of 129
  possessions, 40%, at window 2; 53, 41%, at window 3).

### D3 — Lifespan, stagger, termination, deathbed

- `qi_max` is retuned from the smoke rung's measured spend under p3.0 and
  the chosen `max_tokens` (D10), not from Phase 1. Working assumption for the
  simulator until then: 200,000 *(initial)*.
- **Stagger**: each founder's starting qi is `qi_max × f_i` with `f_i` drawn
  from the new named stream `"lifespan"` uniformly in permille
  `[lineage.founder_qi_floor_permille, 1000]` (initial floor 600). Recorded
  in the founder's AGENT_SPAWNED `qi_max` (already a per-agent key; the
  ledgers, difftest and report read it). Heirs always spawn with the full
  `qi_max`; the stagger persists through birth days. Identical across the
  two arms of a replicate by construction (same master seed). Rejected
  alternatives: staggering founder spawn days (birth day would correlate
  with persona) and an apprentice model that spawns the heir before the
  predecessor dies (changes the constant population). Stage A gives
  `lamarck lifespan` a `--master-seed` option that draws the permille from
  the engine's own `"lifespan"` stream, so every simulated bar uses the
  run's actual stagger, not an analysis seed.
- **Termination**: the run continues while any agent is alive OR a
  succession is pending from the last dusk; it ends only when `world.days`
  is exhausted. (Today it ends when nobody is alive, before the dawn
  succession fires.)
- **Deathbed line**: rendered in the USER prompt (the system prompt is a
  pure function of the persona card and is pinned) when
  `qi ≤ lineage.deathbed_window_days × qi.daily_allowance` (initial 2 × 30k
  = 60k, ≈2.8 days at median spend) — a pure function of the perception view.

### D4 — Succession

- Heir = the predecessor's persona card verbatim, display name
  `"<Name> II"`, `"<Name> III"` … (roman by generation), agent id
  `"<root>g<generation>"` (e.g. `a1g2`; heirs are never loaded from card
  files, so the card-file id regex does not apply). Deterministic; no RNG;
  no model call; identical across arms and replicates; needs no new cards.
- Inheritance vector (pinned in `SuccessionConfig`): stones =
  `economy.starting_stones`; location = predecessor's location at death;
  satchel, journal, notes, reflection empty; qi = full `qi_max`; generation =
  predecessor + 1; root = lineage founder.
- Event layout at the dawn after a death, in spawn order of the dead:
  `DAY_STARTED` → `agent_succeeded {predecessor, successor, root,
  generation, location, born_day}` (world) → `AGENT_SPAWNED {agent_id,
  qi_max, starting_stones, name}` (world; key set unchanged — the world fold
  takes the heir's location from the preceding `agent_succeeded`) →
  `PHASE_STARTED(dawn)`. The day's schedule (`iter_day`) is computed after
  successions; the resume rebuild snapshots `day_alive` at the dawn
  `PHASE_STARTED`.
- One `_LiveEngine.register_agent(card, agent_id, …)` helper is used by
  founder spawn, succession and the resume rebuild (agent ids, personas,
  name lookup, per-agent counters). `_validate_resumable` validates heirs
  against the log's own succession rule, not against card files.
- `agent_died` payload stays `{cause}` (pinned key set); age and generation
  live in `agent_succeeded`.

### D5 — Literacy (the E2 manipulation)

- `lineage.literacy = "oral" | "archive"`. Under `archive`, at every
  `AGENT_DIED` the dead agent's journal (every executed pair → product,
  slag included) is appended to **the stele** of the location where they
  died; it is a pure fold (no event, no action, no cost). Under `oral`
  nothing survives death except what living agents already hold.
- Rendering: a `THE STELE` block for every agent at that location, floor
  content (never dropped), hard-capped at `lineage.stele_render_chars`
  (initial 1,500): most recently deceased first, recipes one line each,
  dead ends grouped, oldest entries truncated first. The dose is therefore
  fixed and always delivered.
- Living agents cannot write to the stele in Phase 2 (PLAN §3.6's paid
  archive notes are deferred with sects to Phase 4 — §10). The construct is
  pre-registered as **journal inheritance**, not literacy-as-choice.
- Sects v1 and the travel-cost raise are removed from Phase 2 (one location,
  zero travel, zero optional actions; a location-bound sect would be one
  sect containing everyone). Locations stay as they are.
- Manipulation checks (pre-registered): in the archive arm, ≥1 stele entry
  rendered to ≥1 heir by the end of the day after the arm's first death
  (the same phrase in E1, E2 and the registration); median `usage_in` per
  call within ±5% between arms (the stele cap makes this achievable); both
  reported.

### D6 — TEACH and STUDY

- `TEACH {target: <compound>, student: <display name>}` — both keys
  mandatory (the parser's exact-key table has no optional keys). Semantic
  validation via one shared `can_teach(actor, student, target)`: the actor
  holds a paid claim for `target`; the student is a living co-located
  other. Failures take the retry path with catalog reasons. Effect: the
  declaration is recorded; it upgrades a later `taught` to `declared: true`.
  Cost unchanged (100 qi surcharge).
- `STUDY` stays a bare billed no-op (enum and cost tables unchanged; the
  scripted mind keeps emitting it). No new `ActionType`. No teaching fee or
  escrow: any payment is an ordinary TRADE.
- Template p3.0: TEACH argument line; a TEACHING rules block ("what you have
  made dies with you unless another cultivator makes it; naming a recipe to
  someone present is how it passes"); the deathbed line (D3); the STELE
  block (D5, archive arm); a lineage line in STATUS (name and generation);
  the stale p1.3 reflection nudge and the "notes are the only memory" line
  are removed. Input-token delta pinned on the golden.

### D7 — Belief commits and claims

- `belief_commit`, Claim nodes, `derives_from(claim → utterance)`, `diff`
  over beliefs, `epidemiology`, meme R0 over hashed claims are **deferred to
  Phase 3** (§10). No second dusk call, no structured reflection.
- Recipe assertions are engine-extracted in the analysis layer (Stage 0
  rule R6: a declarative sentence naming a pair, a product and a yield
  verb, excluding questions and conditionals), checked against the recipe
  truth; false-claim prevalence, persistence and replication by arm are
  locked metrics (D9). `diff <run> <A> <B>` is defined over possessions and
  journals.

### D8 — Provenance store

- Sidecar `provenance.sqlite3` in the run directory (run-dir layout
  deviation, §10), built by `lamarck provenance build RUN_DIR` over
  `EventStore(readonly=True)` (the eventstore's read-only URI rule:
  `immutable=1` unless a `-wal` sidecar exists, then `mode=ro` — a bare
  `mode=ro` open leaves sidecars behind on a checkpointed log); never built
  inside the day batch.
- Idempotence = sha256 over the canonical ordered dump
  `(nodes ORDER BY hash) ++ (edges ORDER BY src, dst, kind)`; that sha is
  what the golden pins and what shallow replay re-checks.
- `blame` exports JSON and DOT; `phylo` and `epidemiology` are out of Phase
  2 (structurally empty in wuxing; deferred with the claim layer, §9, §10).

### D9 — Metrics v1 (formulas locked into SPEC §9b at the freeze)

Per run, per day, per arm; all integers or permille; the same code path
computes the Phase-1 baselines.

| Metric | Definition |
|---|---|
| `living_knowledge(day)` | count, and Σ tier, of compounds held by ≥1 living agent at end of day (holding = a paid claim by a living agent). Ratchet = its AUC over the run plus max living tier by day. |
| `extinction` | a compound whose last living holder dies; `frontier_recovery_days` = days until a living agent next produces it (null if never), with recovery class `transmitted | independent`. |
| `generation` | lineage depth (founder = 1). The run "reaches g" on the first day ≥ ⌈n/2⌉ lineages have a living member of depth ≥ g. Gate: reaches 3. |
| `acquisitions` | per tier: `first_in_world / transmitted / archival / independent` (D1); utterance-rooted share (denominator: all Possession nodes); transmitted-lag histogram. |
| `teaching_compression` | per `taught`: Σ `qi_llm_cost` of the teacher's utterance calls delivered to the student that name any pair of the target's closure, between the first qualifying exposure and the claim (tokens-out reported alongside); median per generation; gen-0 = Phase 1 by the same code. |
| `retread` | per agent per life-day: executed steps whose pair is already in the actor's own journal (`own`), split slag/recipe, permille; `ancestral` = pair present in a dead predecessor's journal or in the stele block rendered to the actor at that step; population by day. Bar: own-journal slag retread of heirs in their first 4 life-days not above founders' first 4 days (same arm); metric baseline = the accepted run. |
| `schedule` | per agent: generation and root (from `agent_succeeded`), birth/death day, alive-days; population: deaths by day, first-death spread per generation, first day ≥⌈n/2⌉ lineages have a living member at depth ≥ g, heir-agent-days with a living member of the previous generation — one function shared with the simulator's bookkeeping, so simulated and measured schedules are comparable. |
| `false_claims` | assertions (D7): prevalence, false permille, replication by another agent within 3 days, persistence (re-asserted after a contradicting outcome). |
| `truncation` | tick calls with `usage_out ≥ max_tokens` permille; actions with a retry permille; qi share on truncated first calls. |
| covariates | qi per agent-day, median `usage_in` per call, lifespan (birth → death) per arm; stele exposure chars per prompt (archive arm); teach declarations count. |

Dropped, with the reason recorded in SPEC §9b: edit-distance fidelity (≡ 1),
mutation trees/rate (≡ ∅), Kaplan–Meier over holders (≡ lifespan), meme R0
over hashed free text (identity ≈ never), Baldwin (Phase 4), diversity
(Phase 3; a $0 reflection-Jaccard proxy is shipped so a baseline exists).

### D10 — `max_tokens` and the retune procedure

Stage 0 reports the truncation statistics. Stage A picks `max_tokens` for
tick calls from the distribution of complete replies (accepted run:
complete tick replies p50 87 / p90 197 tokens; target ≥90% of complete
replies fit; initial guess 400) and revisits `reflection_max_tokens` (160
truncates 97% of reflections). The smoke rung (E1) measures truncation permille
and qi per agent-day under p3.0 + that `max_tokens`; `qi_max` for the
generational canary and E2 is then set by the schedule simulator from the
measured spend so that the median life is ≈10 days, and the bars are
recomputed from the same simulation. Retuning is a config change; both
`live_config_sha` pins move deliberately.

### D11 — Replay compatibility and the version string

- `run_live(..., engine_version=None)`; deep replay passes the log's
  `RUN_STARTED.engine_version`; `resume_live` refuses an engine-version
  mismatch exactly like a template mismatch; `__version__` bumps to
  `0.3.0` at the cutover with the two golden assertions re-pinned. SPEC §9b
  sentence: deep replay is template-bound; shallow replay is forever.
- Archival fixture: the p2.3 live golden scripted run (858 events) is
  committed as `tests/fixtures/golden-p2.3.jsonl.gz`; a test shallow-replays
  it, reports it, and runs the Stage-0 tools on it at every HEAD with pinned
  output shas. A second test does the same on `runs/phase1-accept2` behind
  `skipif(not exists)`.
- A run-end and shallow-replay check asserts `count(llm_texts) ==
  count(LLM_CALL)` with per-row sha re-assert; `llm_texts` retention is
  ratified as mandatory (Phase 3 trains from it).

### D12 — E2 design statement

Two arms (`oral`, `archive`), two replicates each, one universe
(`universe.seed` fixed for all four runs; sampling seeds are inert on the
API, so "paired master seeds" pairs nothing — the design is n=2 unpaired
replicates per arm on one universe, and says so). Each replicate has its own
`world.master_seed`, listed in the registration; within a replicate the two
arms share it (same stagger, same scheduler) and differ only in
`lineage.literacy`, so no two runs share a run id. Fixed arm length per
replicate (set from the generational canary; the same for both arms; no
early stop); the bars are re-simulated under each replicate's actual engine
draw before its go. The archive arm of replicate 1 is the gate candidate and
is run first and alone (Stage F1); it is never substituted; generation 3 is
required of that run, the other arms run to the fixed length. Predicted signs (PLAN
§5): archive ↑ living-knowledge AUC, ↓ extinctions, ↑ frontier recovery
speed, ↑ teacher-qi per transmission (weaker compression pressure), ↑
false-claim persistence. Decision rule: "supported" iff the arm difference
has the predicted sign in both replicates and exceeds the smallest effect
of interest (initial: mean per-day difference in tier-weighted living
knowledge ≥ 6 tier-units over the arm, or ≥1 fewer extinction);
"contradicted" iff both opposite;
otherwise "inconclusive", magnitudes reported either way. Estimability
floors: curves "by generation" need ≥5 `taught` events per generation per
run, else reported as not estimable. Covariates and manipulation checks
(D5) are reported first.

## 4. Stage overview

| Stage | Name | Output | Paid |
|---|---|---|---|
| 0 | Retro-provenance on the Phase-1 log | `lamarck/analysis/{exposure,lifespan,retread}.py` + CLIs, pinned Phase-1 numbers, devlog 002 erratum | no |
| A | Contract freeze (minimal) | `contracts.py` Phase-2 section, typed `[lineage]`, parser TEACH args, `max_tokens` decision, SPEC §9b draft, pin list | no |
| B | Build wave (3 parallel subagents) | B1 mortality/succession/stagger/termination; B2 exposure fold + `taught` + stele + p3.0; B3 provenance sidecar + `blame`/`diff` + metrics v1 | no |
| C | Integration + pre-registration | goldens, resume/replay across a death, fixtures, SPEC §9b, devlog 003 DRAFT with the full registration | no |
| E1 | Smoke rung | 2 arms × 8 days (oral, archive), short lives | ≈$13 |
| E2 | Generational canary | 2 arms × 14 days (oral, archive), production `qi_max` | ≈$21 |
| F1 | Gate run | one 30-day archive arm (replicate 1) | ≈$23 |
| F2 | E2 remaining arms | oral replicate 1, then pair 2 | ≈$70 |
| G | Close-out | devlog 003 final, `v0.3.0` | no |

## Stage 0 — Retro-provenance (this commit)

Deliverables (all $0, all pure folds over a run log):

- `lamarck/analysis/exposure.py` — rules R1–R7 (the D1 matcher, uptake vs
  control, acquisition classes, assertions/misinformation, rendered flag);
  `lamarck exposure RUN_DIR`.
- `lamarck/analysis/lifespan.py` — spend table, schedule simulation with
  stagger and next-dawn replacement, generation/overlap bookkeeping,
  `canary_check`; `lamarck lifespan RUN_DIR --qi-max … --days …`.
- `lamarck/analysis/retread.py` — pair-level own-journal retread per life
  day, known-slag retries, truncation and retry statistics;
  `lamarck retread RUN_DIR`.
- Tests: synthetic hand-computed logs for every rule; determinism over a
  scripted run; the accepted run's numbers pinned behind `skipif`.
- Devlog 002 erratum: the retread finding (§2.6).

Exit: numbers in §2 reproduced by the tools; the D1 rule text is the
matcher's docstring.

## Stage A — Contract freeze

*The coordinator does this alone; everything downstream codes against it.*

First item, before any pin moves: commit the archival fixture
`tests/fixtures/golden-p2.3.jsonl.gz` (the p2.3 live golden scripted run) and
its test (shallow replay + report + the Stage-0 tools, pinned shas), plus the
`skipif` twin over `runs/phase1-accept2`; `replay_live(deep=False)` opens the
store read-only so the test never touches the archived directory.

New event kinds (append-only; SPEC §3 table grows; no existing kind or
payload key set changes):

| kind | actor | payload | emitted |
|---|---|---|---|
| `taught` | world | `{teacher, student, target, tier, utterance_seq, claim_seq, declared, exposures}` | with the student's paying claim (D1) |
| `agent_succeeded` | world | `{predecessor, successor, root, generation, location, born_day}` | dawn after a death, before the heir's `AGENT_SPAWNED` (D4) |

Contract objects: `Technique` (closure form + hash), `ProvenanceNode` /
`ProvenanceEdge` (D2 kinds), `SuccessionConfig`, `LiteracyMode`, `Exposure`
(fold record), metrics result types.

Config: `[lineage]` as a typed field of `LiveWorldConfig` with
`model_config = ConfigDict(extra="forbid")` on the live config (an unknown
section fails at load instead of sharing a run id). The section is optional
with defaults that fold a legacy config identically: `literacy = "oral"`,
`teach_window_days = 3`, `deathbed_window_days = 2`,
`founder_qi_floor_permille = 1000` (no stagger), `stele_render_chars =
1500`; the paid Phase-2 config sets every value explicitly (floor 600). Only
`resume_live` requires recorded `config_sha` equality; finished Phase-1 runs
still shallow-replay under the new schema (tested at the freeze). `[qi]
qi_max`, `[model] max_tokens` and `[model] reflection_max_tokens` per D10;
SPEC §4's stream table gains `"lifespan"`. Template p3.0 is authored and
pinned here in full (D6 text: TEACH line, TEACHING block, deathbed line,
STELE block, lineage line, removals); B2 renders against the pinned text
and may not change wording.

Freeze checklist (the pins that MUST move in the freeze commit, enumerated
so re-pinning is visibly deliberate): `tests/test_contracts.py` event-kind
list; `tests/test_ledgers_p1.py` all-kinds coverage; `Ledgers.apply`,
`fold_balances`, `WorldStateFold.apply`, `report.py` wired for both new
kinds (one synthetic event of every kind folds through all four — a new
test); `VALLEY_LIVE_CONFIG_SHA`, `VALLEY_CLOUD_LIVE_CONFIG_SHA`,
`GOLDEN_RUN_ID` and every chain-derived golden (config schema change);
`GOLDEN_PROMPT_SHA` and the mind goldens (p3.0, once); `test_mind_parser`
TEACH rows; the resume bomb fuse; a `lamarck.eventstore.readonly_uri`
helper shared by `EventStore(readonly=True)`, the prompt loader and the
provenance builder. The archival fixture and the accepted run must still
shallow-replay and report at the freeze commit.

Exit: contracts committed; SPEC §9b draft holds every payload key set and
every rule text from §3; three subagent briefs written against exact names.

## Stage B — Build wave (three parallel subagents)

**B1 — Mortality, stagger, succession, termination** (`engine`, `live`):
the `"lifespan"` stream and founder stagger; `register_agent`; the dawn
succession layout (D4); the termination rule (D3); `_rebuild_engine_state`
and `_validate_resumable` for mid-log spawns; scripted test running three
generations deterministically; resume test killing between a death and its
succession; deep replay across a death byte-identical.

**B2 — Exposure, `taught`, stele, template p3.0** (`engine/world_state`,
`live`, `mind`): the exposure fold (D1 matcher as one shared helper with the
verifier); `_derive_taught` at claim time; TEACH args + `can_teach` (D6); the
stele fold, render and stele-exposure record (D5, D1); the deathbed line
(D3); rendering against the p3.0 text pinned in Stage A, with the
input-token delta recorded; scripted test where a recipe provably crosses a
death via an utterance (tier ≥ 2, student naive) and, in archive mode, via
the stele (class `archival`).

**B3 — Provenance sidecar, `blame`/`diff`, metrics v1** (`lamarck/provenance/`,
`lamarck/analysis/`): builder + canonical dump sha + golden pin; `blame`
(JSON/DOT) resolving every possession to a root on the scripted three
generation run; metrics v1 (D9) with a hand-computable answer for every
formula, including the `schedule` block shared with the simulator; the
`report.json` v2 layout; curve exports (per-day JSON/CSV) as named
artifacts.

Exit: all three green; coordinator merges and resolves fold-order questions.

## Stage C — Integration and pre-registration

- Golden regeneration with a scripted mind that teaches, dies, succeeds and
  (archive arm) reads the stele; every payload key set pinned; the archival
  fixture and the Phase-1 real-log test green.
- Resume and deep replay across deaths and successions; `engine_version`
  override (D11); `__version__ = 0.3.0`.
- SPEC §9b written from the draft plus §10.
- **Devlog 003 DRAFT — the E2 pre-registration, written before any paid
  run**, containing verbatim: the D9 formulas; the predicted signs, SESOI
  and decision rule (D12); estimability floors; the generation definition;
  fixed arm length rule and no-substitution rule; the arms, replicates,
  per-replicate master seeds, fixed universe seed and run order (D12); the
  manipulation checks and covariates (D5); the knobs Stage E may tune (`qi_max`, `max_tokens`,
  `deathbed_window_days`, `stele_render_chars`) with the per-arm
  `live_config_sha` appended before the F1 go; the "unpowered vs null"
  wording; and the gate restated (D2).
- Full gates: pytest, ruff, mypy strict; suite count recorded.

## Stage E — Canary ladder (paid; every schedule bar from the simulator; every arm deep-replayed)

**E1 — Smoke rung** (2 arms × 8 days, one `oral`, one `archive`,
`qi_max = 70,000` with the D3 stagger; p3.0; the chosen `max_tokens`).
Simulated on Phase-1 spend (`lamarck lifespan --qi-max 70000 --days 8
--stagger uniform:600:1000 --stagger-seed 1 --arm-days 8`): first deaths on
days 2–4 (spread 2), 13 deaths inside the arm, heirs acting 33 agent-days,
one lineage at generation 3 on day 7 (half the lineages reach generation 2
by day 4). Before the go the bars are re-simulated under the arm's actual
engine stagger draw and over a spend range of 0.5×–1.0× Phase-1 (the new
`max_tokens` is expected to cut spend), and each bar is set below the worst
case of that range. Schedule bars, per arm (simulator day units, 0-indexed):
≥8 natural deaths by the dusk of day 5 (simulated: 8 by day 4); heirs
acting ≥20 agent-days; first-death spread ≥1 day. Behavioural bars:
≥1 TEACH with a valid target; ≥1 `taught` event; zero unhandled
exceptions; byte-identical deep replay; archive arm: ≥1 stele entry
rendered to an heir by the end of the day after the arm's first death.
Measured, not barred: truncation permille, qi per agent-day, exposure
rendered permille. Output: the E2/F `qi_max`, re-simulated from the smoke's
measured spend.

**E2 — Generational canary** (2 arms × 14 days, one `oral`, one `archive`,
production `qi_max`, initial 200,000 with the D3 stagger). Simulated on
Phase-1 spend: all 8 founder deaths inside the arm (days 6–11), heirs acting
40 agent-days, 24 heir-agent-days with a living founder, generation 2 in
half the lineages by day 9; re-simulated before the go as in E1. Schedule
bars, per arm: reaches generation 2 (D9 definition) by day 10; ≥7 deaths
(simulated 8, all by day 11); heir-agent-days with a living member of the
previous generation ≥ 15 (D9 `schedule`). Behavioural bars: ≥1 `taught`
with `student.generation > teacher.generation` for a target of tier ≥ 2
whose pair the student had never tried (D1 naive); `blame` resolves every
possession; heirs' own-journal slag retread in their first 4 life-days not
above founders'; archive arm: stele rendered to ≥1 heir by the end of the
day after the arm's first death, and ≥1 `archival` acquisition; median
`usage_in` parity within ±5%. Output: the fixed E2 arm length (30 days
unless the simulation on measured spend says otherwise: at 200k, half the
lineages reach generation 3 by day 20).

Any rung failing twice ⇒ stop, diagnose from the log, world-not-prompt fix,
pivot recorded in devlog 003, re-canary (reserve budgeted).

## Stage F — Gate run, then E2

- **F1** one 30-day `archive` arm (replicate 1) with an explicit go. Check
  the gate: reaches generation 3; `blame` resolves every possession; zero
  unhandled exceptions; byte-identical deep replay; E2 curves computed. A
  failure costs this run only; any fix re-enters at E2.
- **F2** the `oral` arm of replicate 1, then replicate 2 (`archive`, `oral`),
  each with its own go at the fixed arm length. The E2 report follows the
  registration verbatim; negative or inconclusive results are written up
  identically.

**Gate (PLAN §4, with the restatement of D2 and D9):** 3+ generations in one
unattended run; `blame` resolves every technique to exactly one root of
kind Utterance or Outcome, end to end; the E2 report with living-knowledge
and extinction/recovery curves; zero unhandled exceptions; byte-identical
deep replay.

## Stage G — Close-out

Devlog 003 final numbers; DRAFT banner removed; `v0.3.0` tagged (user
pushes); memory updated; Phase-3 hand-off note: acceptance logs are Haiku
generated, the retro-train harness on them is cross-model and not E1
evidence; `llm_texts` complete and retained; provenance nodes carry
`llm_seq`; `lamarck fork` and a local-tier p3.0 smoke are Phase-3 backlog.

## 5. Risks and pre-committed mitigations

| Risk | Signal | Mitigation |
|---|---|---|
| Lives too short after the retune (agents die before tier 2) | E1: heirs never claim tier 2 | raise `qi_max` from the simulator; keep the daily allowance fixed |
| Exposure fires but `taught` never does (students hear and never make) | E1/E2: exposures ≫ 0, `taught` = 0 | measure `listener_can_make`; if the closure is the block, the stele (archive arm) is the treatment; the oral arm reports it as the phenomenon |
| Heirs retread ancestors' journals | `own` vs `ancestral` retread (D9 definitions) | ancestral retread IS the E2 phenomenon; only own-journal retread is barred |
| Prompt floor overflow with new blocks | `LMK_ASSERT` in `enforce_budget` | Stage C worst-case floor render test under the paid budget; stele hard cap |
| Truncation rate changes under p3.0 | E1 truncation permille | `max_tokens` chosen from complete-reply lengths (D10); `qi_max` set after E1 |
| Provenance build slow on 30-day logs | build > 10 s | sidecar with (kind, actor) index; perf test on the archival fixture |
| Cost overrun | per-run go numbers below | fixed arm lengths; F1 before F2; reserve line; cut order in §8 |

## 6. Budget (measured: $0.68 per arm-day on the accepted run; +15% for p3.0 blocks)

| Item | Runs | Cost |
|---|---|---|
| E1 smoke | 2 × 8 days | ≈$13 |
| E2 generational canary | 2 × 14 days | ≈$21 |
| Reserve: one re-run of E1 or E2 | | ≈$21 |
| F1 gate run | 1 × 30 days | ≈$23 |
| F2 remaining E2 arms | 3 × 30 days | ≈$70 |
| **Total** | | **≈$148 ceiling, ≈$127 expected** |

Each launch states its own number before the go. Prompt caching does not
apply (the stable system block is far below the API's minimum cacheable
prefix). Wall-clock: ≈40 min per 30-day run.

## 7. Timeline

The phase opened on 2026-08-22 (plan v1); 27 days went to planning, the
review and Stage 0. This is a deliberate re-baseline: the build clock for
Stages A–C starts 2026-09-18 with a 2–3-week estimate, and the ladder is
bounded by §6's budget rather than by time. PLAN §7's kill criterion (any
phase > 2× its estimate ⇒ cut to acceptance criteria only) therefore trips
between 2026-10-16 and 2026-10-30; §8 is the cut.

## 8. Scope cut order (applied mechanically if §7 trips)

1. `diff` CLI and DOT export (keep `blame` JSON).
2. False-claim persistence/replication metrics (keep prevalence).
3. Rendered-exposure analysis from `llm_texts`.
4. E2 replicate pair 2 (report one pair as descriptive).
5. Curve exports as CSV (keep JSON).
Never cut: stagger, succession, `taught`, stele, `blame`, living-knowledge
and extinction metrics, F1.

## 9. Recorded for later phases

- World-rule candidate: the world refuses or prices an experiment step whose
  pair is already in the actor's journal as slag (Phase-1 evidence §2.6).
  Not in Phase 2 — it would confound the retread metric E2 measures.
- Phase 4: sects, paid stele writes, travel economy, realms, costed
  reproduction, `phylo` (needs a universe with route variation).
- Phase 3: belief commits, Claim nodes, consolidation buffer schema
  (provenance nodes carry `llm_seq` already), `lamarck fork`, local-tier
  smoke.

## 10. Deviation ledger for SPEC §9b

| # | Deviation | Kind | Source |
|---|---|---|---|
| 1 | Technique = satchel-free closure; edit-distance fidelity ≡ 1 and route mutation ≡ ∅ in wuxing, both dropped | forced by the universe | §2.1 |
| 2 | `taught` derived from delivered utterances; TEACH is an intent marker | forced by evidence | §2.2, D1 |
| 3 | Blame roots defined (Utterance \| Outcome); gate clause restated | clarification | D2 |
| 4 | `belief_commit`, Claim layer, `diff` over beliefs, `epidemiology`, meme R0 deferred to Phase 3 | scope change vs PLAN §4 | D7 |
| 5 | Sects v1, travel-cost raise, paid archive writes deferred to Phase 4 | scope change vs PLAN §4/§3.6 | D5 |
| 6 | Literacy = journal inheritance via a per-location stele, fixed-size, free | construct change affecting E2 (pre-registered) | D5 |
| 7 | No teaching fee or escrow; any payment is an ordinary TRADE | simplification | D6 |
| 8 | Free 1:1 replacement; deterministic lineage heir; no persona generation; E2 cannot claim selection effects | within PLAN Phase-2 scope; caveat | D4 |
| 9 | Founder stagger via the `"lifespan"` stream (nuisance parameter) | new mechanic | D3 |
| 10 | Termination continues while a succession is pending | mechanic | D3 |
| 11 | Deathbed line in the user render; trigger formula | clarification | D3 |
| 12 | Provenance sidecar file; idempotence as canonical dump sha | run-dir layout vs SPEC §3 | D8 |
| 13 | Metrics replaced per D9; Baldwin (Phase 4) and diversity (Phase 3) deferred | forced / deferral vs PLAN §3.12 | D9 |
| 14 | E2 is n=2 unpaired replicates per arm on one universe; fixed arm length; no substitution; decision rule | design statement | D12 |
| 15 | `engine_version` replay override; `0.3.0` at cutover; deep replay template-bound | clarification | D11 |
| 16 | `llm_texts` retention mandatory with a run-end check | clarification | D11 |
| 17 | No new `ActionType`; TEACH args; STUDY unchanged | constraint | D6 |
| 18 | `agent_died` payload unchanged (v1 proposed extending it) | constraint | D4 |
| 19 | `[lineage]` typed with `extra="forbid"`, optional with legacy-identical defaults | constraint | Stage A |
| 20 | `phylo` deferred to Phase 4 | scope change vs PLAN §4 | D8, §9 |
| 21 | Provenance schema: `Possession` (agent × target) nodes, no edge weight, `llm_seq` on nodes, `Death` as a root kind | forced by D1/D2 | D2 |
| 22 | `report.json` v2 layout supersedes the §9a live layout | layout change | B3 |
| 23 | "Generation" = lineage depth; the run reaches g when ≥⌈n/2⌉ lineages have a living member at depth ≥ g | clarification of PLAN §4 | D9 |
| 24 | Generation 3 is required of the gate run (F1) only; the other E2 arms run to the fixed length | design statement vs PLAN §5 "≥3 generations each arm" | D12 |
| 25 | Stele exposure and the `archival` acquisition class; `derives_from(Possession → Death)` | forced by D5 | D1, D2 |
