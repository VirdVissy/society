# lamarck

**A discovery engine whose searchers are mortal, teachable, and watchable.**

A persistent society of LLM agents in a cultivation-world setting. Agents have finite
lives denominated in tokens, learn verifiable skills against a pluggable "universe"
(a problem domain with an automatic verifier), transmit knowledge only by teaching,
consolidate experience into their own LoRA weights during sleep, and reproduce by
adapter merging. Every belief and technique carries full provenance — the society is
version-controlled like a codebase.

Plan drafted 2026-07-23. Status: Phase 1 closed (`v0.2.0`, 2026-08-21); Phase 2 in
progress — ratified deviations live in `docs/SPEC.md` §9/§9a and the devlogs. This document is the project
constitution; schemas defined here get extracted into `docs/SPEC.md` and locked in
Phase 0. Numbers marked *(initial)* are calibration targets, not measurements — the
honesty bar from loom/sim-ex applies: **measured claims only, negative results get
written up, first unwarmed numbers are never pinned.**

---

## 0. Thesis

Existing agent societies (Generative Agents, Project Sid, Emergence World) do not
learn: "evolution" is text accumulating in a retrieval store, and nothing ever dies,
so there is no selection. Existing discovery engines (FunSearch/AlphaEvolve family)
are already island-model evolution, but the population is *programs* — the LLM is
frozen forever, knowledge is copied rather than taught, and nothing persists between
runs. lamarck occupies the intersection neither side has claimed:

1. **Two inheritance channels, independently dialable.** Cultural (skills must be
   taught in conversation and verified) and genetic (LoRA deltas merged into
   offspring). This is the first testbed for dual-inheritance-theory questions, and
   it is *Lamarckian* — acquired characteristics are heritable, which biology
   forbids and which should make evolution here fast and strange.
2. **Teachability pressure.** A technique survives only if it can be taught.
   Hypothesis: pedagogy forces compression and abstraction, so taught techniques
   generalize better than copied code.
3. **Auditable discovery.** Every technique and belief has a replayable provenance
   chain — who found it, derived from what, verified when. AI-discovery claims
   normally invite skepticism; here every claim ships with its lineage.
4. **Selection is real.** Thinking costs lifespan (qi = tokens). Competence earns
   resources; resources buy life. Realms are engine-enforced capability tiers, not
   labels.

The cultivation skin is not decoration: it is a faithful, legible UI over the real
mechanics (see §10 Glossary — every wuxia term maps to an engine mechanism).

### Headline experiments the system must eventually answer

- **E1** At matched compute and identical experiences, does weight consolidation
  beat note accumulation? (weights vs. notes vs. both)
- **E2** Does literacy change cultural fidelity? (oral-only vs. shared-archive)
- **E3** Dual-inheritance dial: sweep genetic transmission strength α, measure how
  culture compensates.
- **E4** Baldwin effect: does culturally learned skill assimilate into weights
  across generations (teaching-tokens-to-competence falling with lineage depth)?
- **E5** At matched compute, does a *society* out-discover a stateless island-model
  search (FunSearch-style baseline) on the same universe?

Full designs in §5.

---

## 1. Prior art and the novelty ledger

What exists, so we never oversell:

| Territory | Prior art | What they have | What they lack |
|---|---|---|---|
| Agent societies | Generative Agents; Project Sid (PIANO, 1000 agents); Emergence World; AgentSociety | personas, memory streams, governance, scale | no learning in weights, no death/selection, no provenance |
| Sleep-consolidation | "Language Models Need Sleep" (2605.26099); sleep-cycle LoRA blogs | single-agent weight consolidation | no society, no inheritance, no selection |
| Weight-space evolution | PopuLoRA (2605.16727); Sakana evolutionary merge | LoRA populations, merge operators | benchmark self-play only; no personas, culture, persistence |
| Discovery engines | FunSearch; AlphaEvolve; CodeEvolve (2510.14150); Kosmos; Periodic Labs | verifiable rewards, island populations, real results | frozen searcher, copied (not taught) knowledge, no individuals |
| Cultural evolution | telephone-game (2407.04503); donor-game cooperation (2412.10270); Genomebook | transmission-chain experiments | narrow experiments, no tool, no verified skills, no weight channel |
| Belief tracking | Belief Engine (2605.15343); provenance surveys (2606.04990) | stance dynamics, execution provenance | no VCS semantics over belief content; attribution named as open problem |

Claims we are allowed to make if the build succeeds: first society with both
inheritance channels; first teachability-pressure test; first belief store with
git semantics driving training-data curation; first watchable/replayable discovery
engine with mortal searchers.

---

## 2. System overview

```
                        ┌────────────────────────────────────────────┐
                        │                 engine/                    │
                        │  world clock · scheduler · qi ledger       │
                        │  economy · sects · realms · RNG streams    │
                        └───────┬──────────────────────────┬─────────┘
                                │ events (append-only)     │ actions
                        ┌───────▼─────────┐        ┌───────▼─────────┐
                        │   eventstore/   │        │    agents/      │
                        │ SQLite WAL,     │        │ persona · notes │
                        │ hash-chained,   │        │ cognition loop  │
                        │ LLM resp cache  │        │ action protocol │
                        └───────┬─────────┘        └───────┬─────────┘
                                │                          │ generate()
                        ┌───────▼─────────┐        ┌───────▼─────────┐
                        │  provenance/    │        │    serving/     │
                        │ mind-git: nodes │        │ mlx-lm in-proc, │
                        │ edges, commits, │        │ per-agent LoRA  │
                        │ blame/diff/phylo│        │ swap, resp cache│
                        └───────┬─────────┘        └───────┬─────────┘
                                │ curated experiences      │ adapters
                        ┌───────▼──────────────────────────▼─────────┐
                        │              consolidation/                │
                        │  harvest → curate → train (mlx-lm-lora)    │
                        │  → dawn gate (probe battery) → weight commit│
                        └───────┬────────────────────────────────────┘
                                │ verified attempts
                        ┌───────▼─────────┐   ┌────────────────────┐
                        │   universes/    │   │ analysis/ viewer/  │
                        │ Universe API:   │   │ metrics · reports  │
                        │ wuxing alchemy, │   │ lineage graphs ·   │
                        │ combinatorial,  │   │ replay scrubber    │
                        │ (loom kernels)  │   │ (Phase 5)          │
                        └─────────────────┘   └────────────────────┘
```

**The sim-day** (all timing logical, not wall-clock):

1. **Dawn** *(Phase 3+)* — probe battery for any agent that slept; weight commits
   accepted/attenuated/rejected; realm gauntlets run.
2. **Action rounds** — default 8 ticks per agent per day *(initial)*, seeded-shuffled
   order each round. Each tick the agent perceives (world digest + retrieved notes +
   conversation state) and emits one structured action.
3. **Dusk** — each agent emits its belief-ledger update (accepted/rejected/revised
   claims from the day's conversations) and optional reflection note. Costs qi.
4. **Night** — agents whose curated-experience buffer ≥ threshold enter closed-door
   cultivation (LoRA training). Death processing, succession, births.

---

## 3. Specifications

### 3.1 Determinism and event sourcing

The event log is the ground truth; live state is a fold over it (sim-ex philosophy:
the log is the referee).

- **Event record**: `{seq, day, tick, kind, actor, payload, qi_delta, hash}` —
  canonical JSON (sorted keys, no floats in keys, UTF-8 NFC), `hash =
  sha256(prev_hash || canonical_bytes)`. The chain head is the run fingerprint.
- **LLM call record** (a payload kind): `{model_id, adapter_commit, prompt_sha256,
  sampling_params, response, usage_in, usage_out}`. Responses are **recorded**, so
  replay never re-invokes a model. Determinism contract = record/replay, not
  greedy-only sampling (MLX GPU sampling is treated as nondeterministic; the cache
  is the mechanism, the sampling seed is best-effort).
- **Replay**: `lamarck replay <run>` folds the log and must reproduce the identical
  hash chain and identical final-state snapshot hash. Golden replay tests pin this.
- **RNG**: engine streams are SplitMix64-seeded per subsystem from master seed
  `0xDE5EED…` (house convention), never shared across subsystems.
- **Ledger difftest** (sim-ex difftest pattern): qi meters and stone balances are
  maintained live *and* recomputed from the event log by an independent fold;
  any divergence is a hard assertion failure. Assertions always on.

### 3.2 Qi — token metabolism

- Every model call debits the acting agent: `qi_cost = ceil(tokens_in / 4) +
  tokens_out` *(initial)* — thinking/speaking is 4× the price of listening.
- Engine-side actions (moving between peaks, entering tournaments) have fixed qi
  prices in `world.toml`.
- **Lifespan**: agents are born with `qi_max = 1_200_000` *(initial; calibration
  target: median natural life ≈ 30–40 sim-days at Phase-1 activity)*. Qi never
  regenerates. At `qi = 0` the agent dies at next dusk.
- **Daily allowance**: realm-gated cap on qi spendable per day (prevents burning a
  life in one day; creates pacing). R1 = 30k/day *(initial)*.
- Malformed action output → one retry with error feedback (both attempts billed),
  then forfeit the tick. Sloppiness is selected against; no free do-overs.

### 3.3 Agents

**Persona card** (YAML, written at birth, immutable):

```yaml
name: Yan Hua
born: day 0            # founders; children get real birthdays
temperament: "patient, hoards knowledge, fears being forgotten"
values: ["mastery over wealth", "loyalty to lineage"]
quirks: ["explains with cooking metaphors"]
sect: azure-peak       # null for wanderers
lineage: []            # ancestor chain, engine-maintained
```

**Mutable state** (engine-owned): qi, stones, realm, verified-skill set, notes,
rolling self-summary, relationships (engine tracks who has met whom; opinions live
in the agents' own notes/beliefs, not engine state).

**Cognition loop per tick**: context = system (persona + realm + qi/stone meters +
sect) + world digest (location, present agents, notices) + retrieved notes (top-k
by embedding + recency, k=8 *(initial)*) + live conversation. Output = strict JSON
action:

`experiment(task_id, technique_ref | procedure)` · `converse(target, utterance)` ·
`teach(student, technique_ref)` / `study(teacher)` · `trade(target, offer, ask)` ·
`note(text)` · `travel(peak)` · `meditate()` (enter sleep queue early) ·
`challenge(target | tournament)` · `attempt_breakthrough()` · `rest()`

**Notes memory**: agent-authored markdown snippets, embedded (small local embedding
model) into a per-agent store. Facts live here; competence lives in weights (§3.9).
Notes die with the agent (except archive writes, §3.7 literacy).

### 3.4 Universe plugin API

A universe is a problem domain with an instant, incorruptible verifier. Frozen
(after Phase 5) as:

```python
class Universe(Protocol):
    def manifest(self) -> UniverseManifest          # name, tiers, task catalog meta
    def tasks(self, tier: int) -> list[TaskStub]    # ids + public descriptions
    def attempt(self, task_id: str, submission: Submission, rng: Rng) -> Outcome
    # Outcome: {verified: bool, score: float, tier: int, evidence: dict}
    def oracle_audit(self) -> AuditReport           # proves solvability & budgets
```

Rules: `attempt` must be deterministic given `(task_id, submission, rng-stream)`;
verification must cost < 100 ms; the hidden rules must never appear in any prompt.
`oracle_audit` runs a scripted solver to certify every tier is reachable within
realistic qi budgets *before* a run is launched (kills the boring-world failure mode
at config time, not after an overnight run).

**Universe #1 — Wuxing Alchemy** (Phase 1): procedurally generated hidden chemistry.
From a seed: 5 base elements, a directed reaction DAG (depth 6, ~34 reachable
compounds *(initial)*), hidden quality rules (potency/stability as linear functions
+ noise). Task tiers = DAG depth. Submissions are procedures (ordered combination
steps with parameters). Cheap, instantly verified, infinitely re-rollable — the
dev/test universe and the default skin.

**Universe #2 — Constructions** (Phase 5): combinatorial construction/heuristic
problems (packing variants, cap-set-style searches, SAT heuristics) — FunSearch
territory, real open-ended headroom, submissions are small programs run in a
resource-limited subprocess sandbox (CPU-seconds and memory capped, no network).

**Universe #3 — Forge** (stretch): kernel optimization scored by the loom benchmark
harness. Discoveries are actual speedups. Depends on loom bench being callable
headless; do not block v1.0 on it.

### 3.5 Techniques and the teaching protocol

A **technique** is the unit of cultural inheritance:

```json
{"name": "Twin Cinnabar Reduction", "universe": "wuxing", "tier": 2,
 "procedure": ["calcine cinnabar 2x", "quench in water-essence", "..."],
 "params": {"heat": 3}, "claimed_outcome": "compound:vermilion-core"}
```

Canonicalized and content-hashed. A technique is **verified for an agent** only via
that agent's own passing `attempt` event — possession is per-agent and proven, never
copied.

**Teaching**: (1) teacher and student converse (normal qi-billed turns); (2) student
drafts their *own* technique artifact; (3) student attempts the task; (4) pass ⇒
skill granted + provenance edge `taught_by(teacher→student, fidelity)` where
`fidelity = 1 − normalized_edit_distance(teacher_artifact, student_artifact)`.
Teacher payment is whatever they negotiated (free market). Teaching success also
feeds the teacher's consolidation buffer — pedagogy itself trains.

### 3.6 Mind-git — the provenance store

Content-addressed DAG in SQLite. **Node kinds**: `Utterance`, `Claim`, `Technique`,
`Outcome`, `Reflection`, `WeightCommit`, `Birth`, `Death`, `Ascension`. **Edge
kinds**: `derives_from`, `taught_by`, `justified_by`, `contradicts`, `verified_by`,
`consolidated_into`, `child_of`.

- Nodes: `{hash, kind, body_canonical_json, created_seq}`; hash = sha256 of
  canonical body. Edges: `{src, dst, kind, weight, created_seq}`.
- Each agent has a `HEAD`: the set of claims/techniques currently held, updated by
  **belief commits** — the dusk ledger update, where the agent states accepted /
  rejected / revised claims with confidence. The engine canonicalizes, hashes, and
  links each claim to the utterances that carried it (`derives_from`).
- **Operations** (CLI in Phase 2): `blame <claim|technique>` → origin chain to the
  first utterance; `diff <agentA> <agentB>` → belief symmetric difference;
  `phylo <technique>` → transmission/mutation tree across the population (DOT/JSON
  export); `epidemiology <claim>` → adoption curve, R0, mutation events.
- **Weight commits** (Phase 3): every accepted adapter update is a node carrying
  training-set manifest (hashes of the experiences trained on) + gate metrics —
  the implicit mind is versioned alongside the explicit one, and any behavioral
  drift is attributable to a specific night's data.

**Literacy toggle**: `world.literacy = oral | archive`. Under `archive`, agents may
pay qi to write notes into a sect archive that survives their death; under `oral`,
nothing survives death except what was taught. This single switch is experiment E2.

### 3.7 Economy — spirit stones

Sources: discovery bounties by tier `[10, 25, 60, 150, 400]` *(initial)*, ×3 for
first-in-world discovery; tournament prizes; teaching fees (negotiated); trades.
Sinks: experiment materials by tier `[1, 2, 5, 12, 30]`; travel; tournament entry
(20); **life extension** — R4+ may buy `+50k qi per 100 stones` *(initial)*. Life
extension is the loop-closer: competence → wealth → life → more cultivation.
All balances in the ledger difftest.

### 3.8 Sects and realms

**Sects** = named peaks (island model). Founding requires realm ≥ 3. Sect state:
member list, technique-library access rule (members-only / fee / open — chosen by
members via their own conversation, enforced by engine flag), archive (if literate).
Cross-peak travel costs qi ⇒ interaction locality ⇒ divergence pressure. Defection
and marriage-equivalents (cross-sect mentorship) are the migration operator.

**Realms** — engine-enforced capability tiers. Gauntlet = K=3 unseen tasks of the
target tier within a fixed qi budget + canary floor (§3.9). Grants *(initial)*:

| Realm | Name | Grants |
|---|---|---|
| R1 | Qi Condensation | baseline; tier-1 tasks |
| R2 | Foundation | +50% daily qi allowance; tier-2 tasks |
| R3 | Core Formation | context 4k→8k; tier-3; may found a sect |
| R4 | Nascent Soul | life-extension market; tier-4; tournament seeding |
| R5 | Ascendant | *(stretch)* base-model upgrade via distillation (§3.11) |

An LLM will happily roleplay being a Core Formation elder; realms exist only as
verified gauntlet passes. No grant is ever prompt-text alone.

### 3.9 Consolidation — closed-door cultivation (Phase 3)

Cadence: event-driven — sleep triggers when the curated buffer ≥ 64 examples
*(initial)*, not nightly by clock.

**Curation** (the provenance store is the replay buffer; outcomes attach to
exchanges via `verified_by`/`derives_from` edges, giving delayed credit
assignment): dataset mix *(initial)* — 35% SFT on verified successes (situation →
action that worked, incl. teaching successes), 15% DPO pairs (same task, own
success vs. own failure; reallocated to SFT when unavailable), 10% reflections
whose provenance outcome-tags validated, 20% replay of previously consolidated
examples, 20% identity anchors (persona exemplars generated at birth, fixed for
life).

**Training** *(initial)*: LoRA r=16 α=32, attn+mlp projections, lr 1e-5, ≤2 epochs,
seq 2048, via mlx-lm-lora (SFT + DPO supported natively on MLX).

**Dawn gate** (regression tests for souls): (a) skill retention — re-attempt 3 held
skills; (b) persona probes — 10 fixed situational prompts, embedding drift vs.
birth baseline under threshold; (c) canary — fixed 20-item reasoning quiz, score
floor. Accept ⇒ WeightCommit. Fail ⇒ attenuate delta ×0.5, retest once. Fail again
⇒ reject (a survived heart-demon tribulation, logged). Facts stay in notes;
competence goes to weights — never train episodic facts.

**Aging**: lr multiplier anneals with age (young = plastic, old = crystallized);
critical periods emerge rather than being scripted.

### 3.10 Reproduction (Phase 4)

Mentor spends stones + qi to raise a successor. Child adapter:
`TIES(parent_A, parent_B, density 0.5) × α + N(0, σ²)`, defaults `α = 0.3`,
`σ = 1e-4` *(initial)*. α is the dual-inheritance dial and E3's swept variable.
Childhood = 10 sim-days at 3× lr, cosine-annealed to adult by day 30. Persona card
derived from a naming/upbringing conversation with parents. Death: context, notes,
live adapter retired; **all adapters archived** (never deleted) for lineage
analysis — cosine relatedness of adapter deltas vs. genealogy = heritability.

### 3.11 Serving

In-process `mlx_lm` (no server hop): load base once, swap per-agent adapters per
turn (group consecutive same-agent turns to amortize). Base *(initial)*:
`Qwen3-4B-Instruct` 4-bit MLX build; dev tier `Qwen3-1.7B` for fast iteration.
Prompt-cache reuse across an agent's ticks where mlx-lm allows. R5 Ascension
(stretch): distill agent (adapter + curated corpus) into the next base size up —
"getting smarter" becomes literal. Alternative serving path (llama.cpp per-request
LoRA) documented but not built unless MLX blocks us. No network calls from the sim,
ever; hybrid API casting is backlog and off by default.

### 3.12 Observability and analysis

Every run emits `runs/<id>/` with the SQLite log, config snapshot, and a generated
**run report**: discovery timeline, qi/stone ledgers, realm distribution, and the
metric suite. Metrics (locked formulas in SPEC.md at Phase 2):

- **Skill half-life**: survival analysis over holders of each technique.
- **Ratchet rate**: mean verified-technique tier over time + count of parameter
  improvements to existing techniques.
- **Teaching compression**: median teacher-qi per successful transmission, by
  generation. Falling ⇒ emergent pedagogy/jargon.
- **Fidelity**: mean `taught_by` fidelity, by generation and sect.
- **Meme R0 / mutation rate**: from `epidemiology` over claims.
- **Diversity**: mean pairwise cosine distance of probe-battery responses (guards
  the homogenization failure mode).
- **Baldwin metric** (E4): teaching-qi-to-competence regressed on lineage depth.

Viewer discipline: static exports (matplotlib + DOT/D3 JSON) until Phase 5. The
web replay viewer is a Phase-5 deliverable, read-only over the event log.

---

## 4. Phase plan

House pattern: every phase ends with a tagged release, a devlog entry in
`docs/devlog/NNN-*.md`, and pinned numbers. User pushes tags themselves.

### Phase 0 — Foundations (target `v0.1.0`, ~1 focused week)

Goal: a deterministic, empty world that can never lie to us.

- `git init` **inside the project dir** (shields from the stray `~/.git` home repo,
  same trap loom sits in), uv scaffold, Python 3.12, ruff + mypy(strict on
  `engine/ provenance/ universes/`) + pytest, GitHub Actions (lint, types, unit,
  mini-sim golden hash; **no models on runners** — loom pattern).
- Event store (SQLite WAL, hash chain, canonical JSON), config loader
  (`world.toml`), clock/scheduler, qi + stone ledgers, RNG streams.
- `StubAgent` (scripted policy, zero LLM) exercising every action type.
- Golden replay test; ledger difftest; SPEC.md extracted from §3 schemas.

**Accept**: 1,000-day stub sim < 5 s; replay hash-chain identical across runs;
difftest green; CI green. Devlog 001.

### Phase 1 — The Valley Awakens (target `v0.2.0`, ~2 weeks)

Goal: eight strangers survive a month and discover real things.

- mlx-lm serving + response cache wired into the event log; cognition loop; strict
  action protocol with billed retry; 8 handwritten founder personas.
- Wuxing universe v1 + `oracle_audit` (scripted solver proves tier-5 reachable
  within budget before any run).
- Notes memory (embeddings), economy v1 (bounties, materials, trades), CLI
  dashboard (rich), run-report generator v1.

**Accept**: 8 agents × 30 sim-days overnight unattended on the M3 Pro, zero
unhandled exceptions; ≥ 15 distinct verified discoveries incl. ≥ 1 tier-3; full-run
replay byte-identical from cache; first pinned numbers (tok/day, wall-clock/sim-day,
qi calibration) in devlog 002.

### Phase 2 — Lineages (target `v0.3.0`, ~2–3 weeks)

Goal: culture — teaching, death, and a provenance graph you can interrogate.
**First science.**

- Mind-git: belief commits at dusk, `blame` / `diff` / `phylo` / `epidemiology`
  CLIs, DOT/JSON export.
- Teaching protocol + fidelity; death + prompt-level succession (deathbed teaching
  window); sects v1 + travel costs; literacy toggle; metrics suite v1.
- **E2 oral-vs-archive ablation**: paired seeds, 3+ generations each arm.

**Accept**: 3+ generations in one unattended run; `blame` traces any technique to
its origin utterance end-to-end; E2 report with ratchet + half-life curves (negative
result acceptable, written up either way). Devlog 003.

### Phase 3 — Closed-Door Cultivation (target `v0.4.0`, ~2–3 weeks)

Goal: real learning. Weights change; a gate keeps it honest.

- Consolidation pipeline built **offline-first against recorded Phase-2 logs**
  (retro-train harness — debuggable without live sim), then wired live.
- Per-agent adapters + serving swap; dawn gate battery; WeightCommit nodes;
  event-driven sleep; aging lr schedule.
- **E1 weights vs. notes vs. both**: arms share identical day-1 experience via
  yoked replay, then run live at matched compute.

**Accept**: gate rejects 10/10 deliberately corrupted adapters (injection test);
8-agent sleep cycle ≤ 60 min unattended; E1 report with drift curves from the probe
battery. Devlog 004.

### Phase 4 — Bloodlines and Realms (target `v0.5.0`, ~2 weeks)

Goal: evolution proper. The incentive loop closes.

- TIES reproduction (α, σ), childhood plasticity, persona-genesis conversation.
- Full realm ladder: gauntlets, grants, tournaments, life-extension market.
- Relatedness matrix (adapter-delta cosine vs. genealogy), heritability stats,
  E3 α-sweep (3 points), E4 Baldwin tracking begins (long-running).

**Accept**: 5-generation unattended run with ≥ 2 agents at R3+; heritability
computed and plotted; α-sweep report. Devlog 005.

### Phase 5 — The Ten Thousand Worlds (target `v1.0.0`, ~3+ weeks)

Goal: it becomes a tool, and it faces a real baseline.

- Universe API frozen + BYO-universe guide + worked example; Constructions
  universe (sandboxed program submissions: CPU/memory-capped subprocess, no
  network).
- **E5**: implement a FunSearch-style stateless island baseline at matched compute
  on the same universe; society vs. baseline, pinned either way.
- Web replay viewer (read-only scrubber over any run: map, meters, phylo graphs).
- Paper-style README + graded fact sheet (RESUME_FACTS pattern from loom).

**Accept**: E5 result pinned; third-party universe implementable from docs alone
(test: implement the worked example from the guide only); viewer replays any run.
Tag v1.0.0.

### Backlog (post-1.0, unscheduled)

R5 Ascension via distillation to 8B; multi-base-model species (Qwen/Llama/Gemma
sects — does culture homogenize across species?); Forge universe on loom's bench
harness; spectator/streaming mode; API-hybrid "protagonist" casting; internal
prediction markets; Nomic layer (agents amend world rules as sandboxed code).

---

## 5. Experiments ledger

| # | Hypothesis | Design | Primary metric | Confounds to control |
|---|---|---|---|---|
| E1 | Weight consolidation > note accumulation at matched compute | 3 arms (weights/notes/both), yoked day-1 replay, same seeds, same qi | discovery rate; skill retention after distractor period | notes arm gets equal extra context budget; matched token spend |
| E2 | Literacy raises ratchet rate but lowers teaching compression pressure | oral vs. archive, paired seeds, ≥3 generations | ratchet rate; skill half-life; teaching compression | equal qi economy; same founder personas |
| E3 | Culture compensates for weak genetic channel | α ∈ {0.0, 0.3, 0.7}, fixed σ | time-to-recovery of parental skill set in children | childhood length fixed; same curricula |
| E4 | Baldwin: teaching cost falls with lineage depth when α > 0 | within E3 runs, longitudinal | teaching-qi-to-competence vs. lineage depth slope | technique difficulty drift (normalize by tier) |
| E5 | Society ≥ stateless island search at matched compute | same universe, same base model, same total tokens | best-discovery tier; distinct-discoveries count; time-to-tier-k | baseline gets tuned island params (steelman it) |

Reporting rule: every experiment gets a devlog with the *pre-registered* metric
stated before the run, and the result pinned whether it flatters us or not.

---

## 6. Budget and feasibility *(initial estimates, to be measured in Phase 1)*

Hardware: M3 Pro (5P+6E). Base 4B-4bit ≈ 2.3 GB resident + KV; 1.7B dev tier for
iteration. Phase-1 shape: 8 agents × 8 ticks × ~(600 in + 250 out) tokens ≈ 40k
gen-tokens + ~380k prefill-tokens per sim-day → estimated 15–45 min wall-clock per
sim-day on 4B (prefill-dominated; prompt-cache reuse is the first optimization if
this disappoints). Overnight ≈ 10–30 sim-days. Consolidation: 8 agents × ~5 min
LoRA runs ≤ 60 min/night. Storage: 5–20 MB/sim-day events; adapters ~30–60 MB per
weight-commit — archive to external disk after Phase 4 if needed. Cloud spend: $0
by design.

---

## 7. Risk register

| Risk | L | Mitigation | Kill criterion |
|---|---|---|---|
| Nightly signal too weak to move a 4B | M | event-driven cadence (buffer ≥ 64); retro-train harness to tune offline; 1.7B iteration tier | E1 shows no effect at 3× buffer ⇒ publish negative, pivot to prompt-genome evolution (still novel) |
| Catastrophic forgetting / persona collapse | M | dawn gate + attenuation; anchors 20%; replay 20%; lr annealing | gate rejection rate > 50% sustained ⇒ freeze lr, investigate before proceeding |
| Population homogenization | M | diversity metric watched from Phase 3; mutation σ; distinct anchor sets | diversity < 50% of Phase-2 baseline ⇒ raise σ, diversify tasks |
| Boring world (agents flail, nothing discovered) | M | `oracle_audit` gates every config; tier-1 designed trivially findable; qi calibrated to allow ~100 failed attempts per life | audit passes but agents < 3 discoveries/30 days ⇒ prompt/action-protocol rework before adding features |
| Scope creep (viewer, skin content) | H | hard rule: static exports until Phase 5; skin text budgeted (personas + names only) | any phase > 2× its estimate ⇒ cut scope to acceptance criteria only |
| MLX / mlx-lm-lora API churn | M | uv.lock pinned; serving behind `serving/` interface; llama.cpp path documented | — |
| Memory pressure on 36 GB | L | 4-bit base, one base resident, adapters swapped | — |
| Sim results oversold | — | novelty ledger §1; measured-claims-only; pre-registered metrics §5 | — |

---

## 8. Conventions

- Python 3.12, uv, ruff, mypy strict on `engine/ provenance/ universes/`; pydantic
  v2 for all schemas; typer CLI (`lamarck run|replay|report|blame|phylo|audit`).
- Conventional commits; tags `v0.X.0` at phase close; `docs/devlog/NNN-title.md`
  per phase (devlogs read like papers — house style); CHANGELOG.md.
- Assertions always on (`LMK_ASSERT`); no silent fallbacks; canonical JSON is the
  only serialized form; every schema format-locked by tests once SPEC.md lands.
- Seeds `0xDE5EED…`; golden hashes pinned in tests; first unwarmed measurement is
  never pinned (loom lesson).
- All model calls local; no network in the sim loop; response cache mandatory.
- CI: lint + types + unit + stub-sim golden hash on GitHub runners; model-dependent
  e2e runs are local-only and skip in CI (loom pattern for weights).

## 9. Repository layout

```
lamarck/
├── PLAN.md                  # this file
├── docs/{SPEC.md, devlog/, UNIVERSES.md}
├── engine/                  # clock, scheduler, ledgers, economy, sects, realms
├── eventstore/              # sqlite log, hash chain, canonical json, replay
├── agents/                  # personas, cognition loop, action protocol, notes
├── serving/                 # mlx-lm wrapper, adapter registry, response cache
├── provenance/              # mind-git: nodes, edges, commits, blame/diff/phylo
├── consolidation/           # harvest, curate, train, dawn gate, weight commits
├── universes/               # api.py, wuxing/, constructions/, (forge/)
├── analysis/                # metrics, run reports, plots, exports
├── viewer/                  # phase 5: web replay scrubber
├── configs/world.toml       # the world is a config file
├── personas/                # founder cards (yaml)
├── runs/                    # gitignored run artifacts
└── tests/                   # unit, golden replay, property, injection
```

## 10. Glossary — the skin is the mechanism

| Cultivation term | Engine mechanism |
|---|---|
| Qi | lifespan tokens (§3.2) |
| Closed-door cultivation | LoRA consolidation run (§3.9) |
| Heart-demon tribulation | dawn-gate rejection of a bad weight update |
| Qi deviation | catastrophic forgetting |
| Realm / breakthrough | engine-enforced capability tier / gauntlet pass (§3.8) |
| Sect | island subpopulation with locality pressure |
| Secret manual | technique artifact with provenance (§3.5) |
| Master–disciple transmission | teaching-only inheritance, verified per-agent |
| Spirit stones | currency; life extension closes the incentive loop (§3.7) |
| Bloodline | merged LoRA deltas (§3.10) |
| The Dao | the universe's hidden rules, never in any prompt (§3.4) |
| Ascension | distillation into a larger base model (backlog) |
