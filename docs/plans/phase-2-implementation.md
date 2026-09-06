# Phase 2 — Lineages: implementation plan

*Target: `v0.3.0`. Constitution: PLAN.md §4 "Phase 2 — Lineages" and §3.5–3.8,
§3.12. This document decomposes the phase into stages and subagent tasks the
way Phases 0 and 1 were built: frozen contracts first, parallel Fable
subagents against them, integration by the coordinator, pre-registered
acceptance before any paid run. Nothing in this file overrides PLAN.md; where
Phase-1 evidence forces a deviation, it is called out explicitly and must be
ratified into SPEC §9b at integration.*

---

## 0. What Phase 2 is, in one paragraph

Phase 1 proved individual discovery: eight immortal-in-practice agents, each
alone with a task board, found 24/34 compounds. Phase 2 makes it a *culture*:
agents die, teach before they die, found sects, and leave a provenance graph
(mind-git) that lets us trace any technique in the population back to the
utterance that first carried it. The phase ends with the E2 oral-vs-archive
ablation — the project's first pre-registered science — and the acceptance
gate is generational: three-plus generations in one unattended run with
end-to-end `blame` working.

## 1. Ground rules carried forward from Phase 1 (non-negotiable)

- **Event-sourced everything.** Every new subsystem state (beliefs, techniques,
  sect membership, generations) is a pure fold of the hash-chained event log.
  No side stores that can disagree with the chain. Mind-git is a *derived
  index* over committed events, rebuildable from scratch at any seq.
- **Shared-helper symmetry.** Any rule the runner applies (teaching
  verification, succession, sect access) is enforced by the shallow-replay
  verifier through the *same imported helper*, never a re-implementation.
- **Template versioning.** Every prompt-wording change bumps
  `TEMPLATE_VERSION` (p2.3 → p3.0 at the Phase-2 cutover) and re-pins mind
  and live goldens in the same commit.
- **Determinism.** New RNG consumers get named streams; concurrency-invariance
  (waves) must keep holding — `tests/test_live_waves.py` extends to every new
  wave type. Deep replay stays byte-identical, including across resume.
- **World-not-prompt.** Phase 1's core lesson (satchel, auto-claim, journal):
  when agents systematically fail, change the world to match the intuition
  every model already has; prompt text only *states* rules, it never carries
  load-bearing memory.
- **Paid-run protocol.** Scripted backend + suite for all development ($0).
  Canary pair (n=2, pre-registered bar) before any 30-day run. Paid launches
  only on the user's explicit go. `nohup zsh -c 'source ~/.zshrc && exec
  caffeinate …'`, Monitor with failure signatures, deep replay before any
  success claim.

## 2. Phase-1 evidence that shapes Phase-2 design (deviations to ratify)

1. **Techniques are recipes, not prose procedures.** PLAN §3.5 sketches
   techniques as free-text procedures with params. Post-satchel wuxing has an
   exact, canonical form: `{target: str, steps: [[a,b], ...]}` — the minimal
   step list that yields `target` given the holder's satchel. Phase 2 adopts
   the exact form; free-text stays in utterances. This makes fidelity
   computable (edit distance over canonical step lists) and verification
   trivial (the student's own passing attempt, which the auto-claim engine
   already detects).
2. **Lifespan pressure must be configured in — with the corrected
   calibration (2026-09-06 status review).** Devlog 002's original figure
   (~3,200 qi/agent/day ⇒ ≈370-day life) was wrong: no statistic in any run
   reproduces it. Measured total qi per agent-day (thinking + action
   surcharges; both drain lifespan qi): acceptance run `aea04d1a8e6f`
   median **21,721** (min 12,832, max 27,161; 96% of it thinking), cloud
   smoke 15,532, canary-10 arm 2 17,368. So `qi_max = 1,200,000` is a
   ≈55-day life, and the 30,000 daily allowance — median spend is 72% of
   it — is the binding daily constraint. Death has never fired with a real
   model: Phase 1's config makes it unreachable (30 × 30,000 < 1.2M).
   Three-plus generations inside a ≤30-day run needs a life of ≈10
   sim-days: `qi_max` ≈ **200,000–250,000** *(initial; canary-tuned)* ⇒
   expected life 9–12 days at median spend, floor ≈7 days at full
   allowance, ceiling ≈19 days for the thriftiest agent. The originally
   proposed 40,000–60,000 would give 2–3-day lives — exactly the "deaths at
   day 2–3" failure signal the risk table names. Config-only change, but it
   invalidates every Phase-1 behavioral intuition — hence its own canary.

3. **Possession is already proven per-agent.** The once-per-cultivator
   auto-claim ledger IS the skill registry: agent X "has" technique T iff X
   has a paid claim for T's target. Teaching grants nothing by fiat; it ends
   in the student producing the compound themselves (satchel + journal make
   this a real, checkable act). The `taught_by` edge is engine-derived, not
   model-asserted.
4. **Waves change conversation timing.** Teaching dialogues span rounds (speech
   lands next round). The teaching protocol below is therefore asynchronous by
   design — no same-round call-and-response is ever required.

### Pre-Stage-A findings (2026-09-06 status review)

- A `[lineage]` TOML section is silently ignored today and does not change
  `live_config_sha`/run_id (`LiveWorldConfig` has no such field; pydantic
  drops unknown sections). Stage A must add it as a typed field before any
  config-only lineage experiment, or two different worlds share a run id.
- The persona pool is exactly 8 cards for 8 founders and the loader rejects
  duplicate display names, so B2's successor spawn needs both more cards
  and a lineage-safe naming scheme ("Yan Hua II") that passes that check.
- "Retread rate" is a hand-computed devlog number, not a metric in
  `lamarck/analysis/`; the Stage-E bar depends on it, so C2 must add it
  (baseline: canary10-1/-2, 12 and 16 distinct over 4 days).
- The paid config's fingerprint is now pinned
  (`tests/test_config_live.py::VALLEY_CLOUD_LIVE_CONFIG_SHA`); the Stage-A
  retune re-pins it deliberately.
- `__version__` is hashed into every RUN_STARTED and deep replay re-emits it
  from the running code, so bumping the version string breaks byte-identical
  replay of every existing log. Stage A/D should decide whether replay reads
  `engine_version` from the log (preferred) before any bump.

## 3. Stage overview

| Stage | Name | Output | Paid? |
|---|---|---|---|
| A | Contract freeze | `contracts.py` §Phase-2 + SPEC §9b draft | no |
| B | Build wave 1 (3 parallel subagents) | mind-git store; mortality+succession; sects+literacy | no |
| C | Build wave 2 (2 parallel subagents) | teaching protocol in the runner; CLIs + metrics suite | no |
| D | Integration | goldens, replay/resume extensions, docs, devlog 003 draft | no |
| E | Canary ladder | mortality canary → generational canary (n=2 each) | ~$25 |
| F | E2 ablation + acceptance | paired-seed oral/archive runs + 30-day acceptance | ~$45–65 |
| G | Close-out | devlog 003 final numbers, `v0.3.0` tag | no |

Stages B and C are separated because C's subagents consume B's frozen fold
APIs; within each stage the subagents are independent and run in parallel,
all on Fable, max effort, per the house pattern.

---

## Stage A — Contract freeze

*The coordinator does this alone; everything downstream codes against it.*

**New event kinds** (append-only extension of the envelope; SPEC §3 table
grows, no existing kind changes):

| kind | actor | payload (locked at freeze) | emitted |
|---|---|---|---|
| `belief_commit` | agent | `{accepted: [claim…], rejected: [claim…], sources: {claim_hash: [seq…]}}` | dusk, after reflection |
| `technique_drafted` | agent | `{target, steps, drafted_from: seq \| null}` | on a valid TEACH/STUDY exchange (engine-derived) |
| `taught` | world | `{teacher, student, target, fidelity_permille, technique_seq, claim_seq}` | when the student's first claim of a taught target lands |
| `agent_died` | agent | `{cause: "qi_exhausted", age_days, satchel, journal_size}` | dusk (exists since P0; payload extended) |
| `agent_succeeded` | world | `{predecessor, successor, inherited: {notes?: […], archive?: bool}}` | dawn after a death |
| `sect_founded` / `sect_joined` / `sect_left` | agent | `{sect, …}` | on verified action |
| `archive_written` | agent | `{sect, text}` (literacy=archive only) | on paid NOTE-to-archive |

**Contract objects** (frozen dataclasses/pydantic in `contracts.py`):
`Technique` (canonical form + content hash via the one true serializer),
`ProvenanceNode`/`ProvenanceEdge` (node kinds `Utterance, Claim, Technique,
Outcome, Reflection, Birth, Death`; edge kinds `derives_from, taught_by,
justified_by, verified_by, child_of` — `WeightCommit`/`consolidated_into`
reserved, Phase 3), `SuccessionConfig`, `SectConfig`, `LiteracyMode`
(`oral | archive`), and the metrics-suite result types.

**Config additions** (`[live]` extensions + new `[lineage]` section):
`qi_max` retuned; `succession = "spawn_heir"` *(prompt-level: heir persona
generated from a deathbed conversation transcript + predecessor's name
lineage)*; `deathbed_window_days = 2` (low-qi agents get a TEACHING urgency
line — a stated rule, not new mechanics); `literacy = "oral" | "archive"`;
`archive_write_cost = 5` stones *(initial)*; `sect_founding_realm` deferred
to Phase 4 (Phase 2 sects are location-bound: founding requires N co-located
agreeing agents *(initial: 2)* — realms don't exist yet, deviation to ratify).

**Freeze checklist:** config sha pins updated; every payload key set named in
SPEC §9b draft; `wave_concurrency` semantics reviewed against every new
wave; the Phase-1 golden must still pass untouched at freeze (pure addition).

Exit: contracts committed; subagent briefs written referencing exact names.

---

## Stage B — Build wave 1 (three parallel subagents)

### B1 — Mind-git: the provenance store (`lamarck/provenance/`)

The content-addressed DAG as a *derived index*: `provenance/build.py` folds
any event range into nodes+edges tables (same SQLite file, separate tables,
never hashed into the chain — droppable and rebuildable like `llm_texts`).

- Node hashing: sha256 of canonical body; nodes for every utterance (from
  CONVERSE actions), claim (from `belief_commit`), technique, attempt
  outcome, reflection, birth, death.
- Edge derivation rules (pure functions of the log, locked by tests):
  `derives_from(claim → utterance)` from `belief_commit.sources`;
  `verified_by(technique → outcome)` from the claim ledger;
  `taught_by(student_technique → teacher_technique)` from `taught` events
  with `fidelity` as edge weight; `child_of(successor_birth → death)`.
- Incremental build: `build(upto_seq)` is deterministic and idempotent;
  rebuilding from scratch must byte-match an incremental build (test).
- **No LLM anywhere in this package.** Pure fold, sub-second on Phase-1-size
  logs (perf test against `runs/phase1-accept2`, 7,392 events).

Deliverables: package, builder CLI (`lamarck provenance build RUN_DIR`),
~40 unit tests incl. golden-DAG pins over a scripted run.

### B2 — Mortality, succession, and the deathbed (`engine` + `live` touches)

- Lifespan economy: retuned `qi_max`; death check exists since P0 — what is
  new is *population continuity*: at dawn after a death, the engine spawns a
  successor (next persona from an extended persona pool; name carries
  lineage: "Yan Hua II"), emitting `agent_succeeded`. Population is
  closed-loop constant *(initial: replacement 1:1)*.
- Prompt-level inheritance *(oral mode)*: the successor starts with empty
  satchel/journal/notes — what survives is only what was *taught* (already
  in living agents' state) plus the persona lineage. Under `archive`, the
  sect archive is readable by members — the E2 manipulation.
- Deathbed window: when projected lifespan ≤ `deathbed_window_days`, the
  system prompt gains one urgency line (template p3.0) telling the agent its
  qi is failing and untaught knowledge dies with it.
- Scheduler: death/succession stay in the dusk/dawn world slots — no wave
  interaction; RNG for successor personas from a new named stream
  (`"succession"`).

Deliverables: engine fold + runner integration behind a scripted-backend
test that runs 3 generations deterministically; resume must handle
mid-generation boundaries (test: kill between death and succession).

### B3 — Sects, travel pressure, literacy (`engine/world_state.py` + actions)

- Sects v1: named peaks = existing locations; `sect_founded/joined/left`
  actions with runner validation (co-location, consent via prior utterance
  is NOT required — joining is unilateral *(initial)*, access rules deferred);
  sect membership folds into world state and renders as a perception block.
- Travel cost raise *(initial: 500 → 2,000 qi)* — the divergence pressure
  PLAN §3.8 wants; canary-tunable.
- Literacy toggle: `oral` (Phase-1 behavior, notes die with the agent) vs
  `archive` (a NOTE variant targeting the sect archive, costs stones,
  survives death, readable by sect members — one new perception block,
  budget-droppable before the journal).

Deliverables: folds + validation + perception blocks + ~30 tests; the
shallow-replay verifier re-derives sect membership and archive contents.

Exit for Stage B: all three land on green suite; coordinator merges,
resolves fold-order questions, and freezes the fold APIs for Stage C.

---

## Stage C — Build wave 2 (two parallel subagents)

### C1 — The teaching protocol in the runner

Teaching is deliberately thin in the engine — culture should be emergent:

1. Teacher and student converse normally (qi-billed, satchel/journal give
   the teacher real content to transmit). TEACH action *(now with args:
   `{target: <compound>, student: <name>}`)* declares intent and is the
   provenance anchor; STUDY declares receptivity. Both stay cheap.
2. The engine derives `technique_drafted` from the teacher's declared target
   (their minimal journal path to it — engine-computed, canonical).
3. When the *student* later produces that target (auto-claim fires), the
   engine emits `taught` with `fidelity = 1 − normalized_edit_distance(
   teacher_path, student_path)` over canonical step lists, and the mind-git
   edge appears. No new model calls anywhere in this flow.
4. Teaching fees: whatever agents negotiate via existing TRADE. No engine
   escrow *(deviation from PLAN's "negotiated payment" being enforced —
   ratify)*.

The load-bearing insight: *the world already verifies teaching* (satchel +
auto-claim); the protocol only attributes it. All validation shared with the
replay verifier via one helper (`_derive_taught`).

Deliverables: runner integration, parser extension (TEACH/STUDY args),
template p3.0 prompt sections (TEACHING rules block, deathbed line, sect
block, archive block), golden re-pins, scripted 3-generation teaching test
where a technique demonstrably crosses a death boundary.

### C2 — CLIs and the metrics suite v1 (`lamarck/analysis/`)

- `lamarck blame <run> <technique|claim>` → origin chain to first utterance
  (walk `derives_from`/`taught_by` to the root; DOT + JSON export).
- `lamarck diff <run> <agentA> <agentB>` → belief/technique symmetric
  difference at a seq.
- `lamarck phylo <run> <technique>` → transmission/mutation tree (DOT/JSON).
- `lamarck epidemiology <run> <claim>` → adoption curve, R0, mutations.
- Metrics suite v1 with **locked formulas into SPEC** (skill half-life via
  Kaplan-Meier over holders; ratchet rate; teaching compression =
  median teacher-qi per successful transmission by generation; fidelity by
  generation; meme R0; diversity deferred to Phase 3 — needs probe battery).
- All of it pure folds over the log + provenance index; deterministic
  byte-identical reports (the Phase-1 report discipline).

Deliverables: CLIs + `report.json` v2 + golden metric pins over a scripted
run with a known hand-computable answer for every formula.

Exit for Stage C: suite green; `blame` traces a scripted technique
end-to-end across two generations — the acceptance criterion, proven for $0.

---

## Stage D — Integration (coordinator)

- Golden regeneration: new scripted minds exercising teach/study/death/
  succession/sect/archive paths; every payload key set pinned; concurrency-
  equivalence and snapshot-semantics tests extended to any new wave.
- Resume: `_rebuild_engine_state` extended for all new folds + RNG streams;
  the interrupted-then-resumed deep-replay test extended across a death.
- SPEC §9b written from the draft + ratified deviations (technique form,
  unilateral join, no fee escrow, location-sects, lifespan retune).
- Devlog 003 opened DRAFT with the E2 pre-registration **written before any
  paid run**: hypothesis (PLAN E2), arms, paired seeds, primary metrics
  (ratchet rate; skill half-life; teaching compression), and the acceptance
  gate restated verbatim.
- Full gates: pytest (target: Phase-1's 506 + ~150 new), ruff, mypy strict.

---

## Stage E — Canary ladder (paid, ~$6, each rung gated on the last)

*Bar pre-registered here; n=2 arms per rung; every arm deep-replayed.*

1. **Mortality canary** (2 arms × 6 days, canary-only `qi_max` ≈ 90,000 so
   a life is ≈4 days at measured spend — this rung exercises death +
   succession *mechanics*, not the production lifespan; no E2 split):
   bar = ≥1 natural death per arm; succession fires; the successor takes ≥1
   action; zero unhandled exceptions; deep replay byte-identical. Tunes:
   deathbed window. *(Amended 2026-09-06: at the corrected calibration a
   6-day arm at the production `qi_max` cannot contain a natural death.)*
2. **Generational canary** (2 arms × 12 days, production `qi_max`
   200,000–250,000): bar = ≥2 generations per arm; ≥1 `taught` edge per
   arm (a technique provably crosses a death); `blame` resolves it
   end-to-end on the real run; retread rate (now a C2 metric) not
   regressed vs canary10 (journal still working under mortality pressure).
   *(Amended 2026-09-06: 10 days was marginal for 9–12-day lives.)*

Any rung failing twice ⇒ stop, diagnose from the log, world-not-prompt fix,
new pivot in devlog 003, re-canary. (Phase-1 history says expect at least
one such pivot; budget for it.)

## Stage F — E2 + acceptance (paid, ~$45–65, explicit go per run)

- **E2 oral-vs-archive**: 2 paired master seeds × 2 arms (oral/archive) ×
  ~25–30 days (enough for 3+ generations at the tuned lifespan) ≈ 4 runs
  ≈ $40–55. Pre-registered primary metrics from the Stage-D registration;
  negative/null results written up identically (PLAN reporting rule).
- **Acceptance run**: the archive-arm run doubles as the acceptance run if
  it meets the gate; otherwise one dedicated 30-day run (~$18).
- **Gate (PLAN, verbatim, does not move):** 3+ generations in one unattended
  run; `blame` traces any technique to its origin utterance end-to-end; E2
  report with ratchet + half-life curves; zero unhandled exceptions;
  byte-identical deep replay (house standard).

## Stage G — Close-out

Devlog 003 final numbers + E2 report; DRAFT banner removed; `v0.3.0` tagged
(local; user pushes); memory + backlog updated; Phase-3 preconditions noted
(the provenance store is Phase 3's replay buffer — its schema quality gets
audited here, before weights depend on it).

---

## Risks and pre-committed mitigations

| Risk | Signal | Mitigation |
|---|---|---|
| Lifespan retune breaks the discovery economy (agents die before earning) | mortality canary: deaths at day 2–3, no tier-2s | raise `qi_max` stepwise; keep daily allowance fixed (it, not qi_max, sets thinking depth) |
| Teaching never happens organically | generational canary: zero `taught` edges | deathbed line strengthens FIRST; if still zero, world-fix: make TEACH declare-target mandatory arg (already designed) and re-canary; never pay agents to teach by fiat |
| Successors retread ancestors' journals (journal dies with the agent — by design in oral mode) | retread rate spike in gen-2 | this IS the E2 phenomenon (archive arm keeps journals in the sect archive); do not "fix" it — measure it |
| Provenance fold slow on 30-day logs | build > 10 s | incremental build + index on (kind, actor); perf test pinned in Stage B1 |
| Wave semantics break teaching dialogue | students answer stale questions | protocol is async by design (no same-round dependency); snapshot-semantics test extended to TEACH |
| Cost overrun | E2 needs 4 long runs | arms may run at 25 days if generations land by then; per-run explicit go keeps the user in control |

## Budget and timeline summary

- Build (Stages A–D): $0 API, the bulk of the calendar time; 5 subagent
  briefs, 2 waves, ~150 new tests.
- Paid (Stages E–F): ~$70–90 total, ~6–10 runs, each behind an explicit go;
  wall-clock per 30-day run ≈ 30–40 min at current wave throughput.
- Everything pinned as it lands; devlog 003 is written alongside, not after.
