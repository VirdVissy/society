# lamarck SPEC — locked formats and semantics

Formats in this document are **locked**: changing any of them is a
breaking change that requires a devlog entry, a migration note, and updates to
the format-lock tests that pin them. Phase 0 locks §1–§6. Later phases append;
they do not silently rewrite.

Source of truth for types: `lamarck/contracts.py`. This document explains and
freezes; the contracts file compiles.

---

## 1. Canonical JSON

The only serialized form for anything hashed, stored, or compared.

- Values: `str | int | bool | None | dict[str, …] | list[…]`. Nothing else.
- **Floats are rejected** (`CanonicalError`), even integral ones. Fractions are
  scaled integers by convention (`*_permille`). NaN/Inf are impossible by
  construction.
- All strings — keys and values — are **NFC-normalized** before encoding.
- Keys: `str` only, sorted by codepoint after normalization.
- Encoding: `json` with separators `(",", ":")`, `ensure_ascii=False`, UTF-8.
- Determinism guarantee: `canonical(x)` is byte-identical across runs,
  platforms, and Python patch versions (property-tested: idempotence and
  parse→re-encode round-trip).

Edge cases the rules above do not pin are **rejected, never guessed at**
(locked by tests): distinct keys that collide after NFC normalization
(output would depend on insertion order); strings containing lone
surrogates; nesting beyond the recursion limit or self-referential
containers; `bytes`, tuples, sets, plain `Enum`, arbitrary objects,
non-`str` dict keys. `StrEnum` values encode as their underlying string,
`IntEnum` as its integer, both via the base type's data (overridden
`__str__` is bypassed).

Implementation: `lamarck/eventstore/canonical.py`
(`canonical_bytes`, `sha256_hex`, `CanonicalError`).

## 2. Event envelope and hash chain

```
envelope_i = {"seq": i, "day": d, "tick": t, "kind": k, "actor": a,
              "payload": p, "qi_delta": q, "stones_delta": s}
hash_i     = sha256_hex( utf8(prev_hash_hex) || canonical_bytes(envelope_i) )
```

- `prev` of the first event (`seq = 0`) is `GENESIS_HASH = "0" × 64`.
- The chain head `(last_seq, last_hash)` is the **run fingerprint**.
- Wall-clock timestamps live in an unhashed side column (`wall_ts`) and are
  never part of any hashed or compared state — replay is time-independent.
- Store: SQLite, `journal_mode=WAL`, single-writer, append-only. Schema:
  `events(seq PK, day, tick, kind, actor, payload, qi_delta, stones_delta,
  hash, wall_ts)`.
- `verify_chain` recomputes every hash from genesis and re-canonicalizes every
  payload; any divergence is a hard failure. Tampering with any committed byte
  changes the fingerprint.
- The store is file-backed only (WAL is asserted; no `:memory:`), single
  writer. `append` NFC-normalizes actor/payload strings — the **returned**
  record, not the submitted draft, is committed truth and is what consumers
  fold. Delta columns are 64-bit; payload integers are arbitrary-precision.
- Known limit (recorded, accepted): the chain proves integrity and order of
  what is present, not the absence of tail truncation. External head evidence
  (`report.json`, the golden pins) is the truncation witness.

## 3. Event kinds (Phase 0)

| kind | actor | payload | notes |
|---|---|---|---|
| `run_started` | world | `run_id`, `config_sha`, `master_seed`, `schema_version`, `engine_version` | first event of every run |
| `day_started` | world | `day` | resets daily qi-spend meters |
| `phase_started` | world | `day`, `phase` ∈ dawn/action/dusk/night | structure marker |
| `agent_spawned` | world | `agent_id`, `qi_max`, `starting_stones` | balances initialize from payload; deltas zero |
| `action` | agent | `type` ∈ ActionType, args; degraded actions add `degraded: true`, `wanted` | qi cost in `qi_delta` (negative); materials in `stones_delta` |
| `ledger_adjust` | agent | `reason` (e.g. `"bounty"`), `tier` | engine-side credit/debit via envelope deltas |
| `agent_died` | agent | `cause` (e.g. `"qi_exhausted"`) | emitted at dusk |
| `run_finished` | world | `days_elapsed`, `final_state_sha` | `final_state_sha` = sha256 of canonical `LedgerBalances` |

ActionType (11, locked): `experiment, converse, teach, study, trade, note,
travel, meditate, challenge, attempt_breakthrough, rest`.

**Run event layout** (locked; full normative text in the `lamarck/sim.py`
module docstring, frozen by the golden fingerprint test):
`RUN_STARTED` → `AGENT_SPAWNED × N` (spawn order `a1..aN`, day 0 tick 0) →
per day: `DAY_STARTED`, phase markers at transitions (4/day), ACTION slots
with affordability checked allowance-then-stones (degraded slots bill REST,
or nothing plus `free: true` when even REST exceeds the remaining
allowance; degraded slots never draw from the stub-universe stream), dusk
deaths in spawn order, periodic difftest → `RUN_FINISHED` (last day, night
tick) with `final_state_sha` = sha256 of the canonical `LedgerBalances`
dump. Preamble in its own batch, one batch per day, `RUN_FINISHED` outside
any batch. `run_id = sha256_hex(config_sha + ":" + master_seed_hex)[:12]`.

**Run directory** (locked): `events.sqlite3` (ground truth), `config.toml`
(the *effective* config — verbatim source copy, or a synthesized dump
asserted to round-trip to the same `config_sha` when overrides were
applied), `report.json` (convenience; strings and ints only, wall time as
integer ms; keys: `run_id, days_elapsed, events, head_seq, head_hash,
final_state_sha, wall_ms, alive_count, qi_total, stones_total`; never
hashed, never read by replay).

## 4. RNG substreams

```
name_tag    = u64_be( sha256(utf8(name))[0:8] )
stream_seed = splitmix64( master_seed XOR name_tag )
stream      = random.Random(stream_seed)
```

- `splitmix64` is the published reference algorithm; pinned vectors in
  `tests/test_rng.py` (seed 0 → `0xE220A8397B1DCDAF`).
- Substreams are memoized per name, never reseeded mid-run, never shared
  across subsystems. Phase-0 canonical names: `"scheduler"`,
  `"stub-universe"`, `"agent:{agent_id}"`.
- Master seed family: `0xDE5EED…` (default `0xDE5EEDDE5EEDDE5E`).

## 5. Sim-day structure and scheduler

Per day: one `DAWN` world slot → `rounds_per_day` rounds of per-agent `ACTION`
slots (`ticks_per_agent_per_round` each; order = seeded shuffle of alive
agents from the `"scheduler"` stream, exactly one shuffle per round) → one
`DUSK` world slot → one `NIGHT` world slot. Tick indices are contiguous from
0 within each day. Two runs with identical config and seed produce identical
slot sequences.

Locked interpretation details: world slots (dawn/dusk/night) carry
`round = 0` — the round field is meaningful only on `ACTION` slots. An
agent's `ticks_per_agent_per_round` ticks are **consecutive** within its
round. The scheduler stream is stateful across days (one sequence per run,
never reseeded); the whole day is computed at `iter_day` call time, so stream
consumption depends only on the call sequence. `alive_ids` is shuffle input:
callers pass it in spawn order.

## 6. Phase-0 stub semantics (replaced by real semantics in Phase 1+)

- Every action costs flat qi per ActionType (`[qi.action_costs]`) — the
  stand-in for token metering.
- `EXPERIMENT {tier ∈ 1..5}` additionally requires `materials[tier-1]` stones.
  Success drawn from `"stub-universe"`: uniform int in `[0, 1000)` `<`
  `stub_success_permille[tier-1]` → follow-up `ledger_adjust` crediting
  `bounties[tier-1]` stones (reason `"bounty"`).
- **Degradation rule**: an action the agent cannot afford (stones, or daily
  qi allowance `[qi.daily_allowance]`) degrades to `REST`, recording
  `{degraded: true, wanted: …}`. No free retries; sloppiness costs.
- **Death**: at dusk, any agent with `qi ≤ 0` dies (`agent_died`,
  `cause = "qi_exhausted"`). Dead agents take no further slots. Run ends
  after `days` or when all agents are dead.
- Stones never go negative (engine assertion). Qi may go negative between
  action and dusk.

## 7. Difftest (ledger integrity)

Two independent implementations of balance accounting must agree at every
dusk and at run end:

1. **Live**: `Ledgers.apply(event)` as events commit.
2. **Fold**: `fold_balances(events, cfg)` — a from-scratch fold over the log
   sharing no code path with the live ledger.

`assert_difftest` compares full `LedgerBalances` (qi, stones, alive) and
fails hard with a per-agent diff. This is the sim-ex difftest pattern: the
value is that the implementations *can* disagree.

Cadence: the fold is a full log re-scan (O(n)), so the runner difftests
**periodically** (`difftest_interval` days, default 10) and **always at run
end** — never every dusk on long runs (that would be O(n²)).

Hardening beyond the letter of the contract (locked): a *committed* ACTION
whose spend exceeds the daily allowance trips an always-on assertion in the
live ledger — degradation is the producer's job, and a recorded
over-allowance spend is a producer bug, never silently tolerated. Spawn,
death, and world events must carry zero deltas. Negative `qi_delta` on
`ACTION` and `LLM_CALL` events counts as allowance spend (Phase 1: thinking
bills on the model call, physical surcharges on the action).

## 8. Replay contract

`lamarck replay <run_dir>` must, with no model calls and no wall-clock
dependence: verify the hash chain from genesis, refold ledgers via the
independent `fold_balances`, recompute `final_state_sha`, and compare both
the chain head and the state sha against the `run_finished` payload. Exit
code 0 iff everything matches. Replay's code path has zero model of the
sim — no Scheduler, no StubPolicy, no RNG — so it cannot share a bug with
the producer. A run is *reproducible* iff replay passes; the golden test
pins the chain head of a fixed config+seed so any semantic drift in engine
code is caught as a hash change.

## 9a. Phase 1 additions (locked at v0.2.0)

Contracts: `lamarck/contracts.py` "PHASE-1 LIVE SEMANTICS" is normative for
billing, the action protocol, and live action args. Decisions ratified from
the build waves:

- **LLM_CALL** events carry `qi_delta = -qi_llm_cost(in, out)` =
  `-(ceil(in/4) + out)`; `stones_delta` must be zero. `TASK_ATTEMPT` and
  `REFLECTION` are zero-delta, living-actor events.
- **Prompt envelope**: the string passed to any backend is canonical JSON
  `{"system", "template", "user"}` with `TEMPLATE_VERSION = "p1.0"` (any
  wording change bumps it; goldens catch unversioned drift).
  `prompt_sha256 = sha256(envelope)`. Full prompt texts live in the unhashed
  `llm_texts` side table (`lamarck/eventstore/texts.py`): dropping that
  table cannot change any fingerprint, and writes must land outside
  `EventStore.batch()` windows (single-writer file).
- **Utterance delivery**: a non-degraded CONVERSE delivers at emission to
  *living* co-located others (a later traveler keeps what they heard;
  non-co-located agents never receive it). Perception shows the ≤ 6 most
  recent with `day ≥ current_day − 1`, oldest first.
- **Degraded events short-circuit**: the world fold ignores degraded
  CONVERSE/TRAVEL/NOTE payloads entirely; `wanted` payloads of degraded
  actions are not validated (the runner's retry path owns arg validity).
- **Parser**: failure-reason catalog and the first-balanced-brace rule are
  locked by tests; `MAX_ACTION_PARSE_RETRIES = 1`, second failure forfeits
  as `{type: "rest", degraded: true, reason: "malformed"}`.
- **Wuxing**: 34 compounds over tiers {1:8, 2:8, 3:7, 4:6, 5:5}; one recipe
  per compound (unordered pair, unique across the universe, ≥ one
  ingredient of tier t−1); non-recipe pairs yield `slag`; `slag` is
  absorbing. Submissions cap at 12 steps; generation enforces
  producibility (a recipe's closure must fit `MAX_STEPS − (tiers − tier)`),
  so every commission is winnable — `oracle_audit`/`lamarck audit` proves
  it before a run. `attempt` is rng-free; `verified` = target appears in
  `step_products` (mid-procedure counts). No public surface (manifest,
  tasks, titles, outcomes) may reveal a recipe or name an unproduced
  compound other than the task target.
- **Config fingerprints**: the Phase-0 `config_sha` of `configs/world.toml`
  is regression-pinned (`24cbbf0e…`); live runs fingerprint the extended
  model via `live_config_sha` (valley golden `3723764d…`).
- **Once-per-cultivator bounties** (ratified from acceptance-run evidence):
  the board pays a commission's bounty at most once per agent — repeat
  verifications by the same agent commit a verified TASK_ATTEMPT but no
  LEDGER_ADJUST. Rationale: five observed days of unlimited repeat pay
  produced rational bounty farming (20 verifications over 2 distinct
  commissions, frontier flatlined). First-in-world ×3 unchanged;
  cross-agent verification still pays base once. The shallow-replay
  verifier enforces it (a re-paying adjust is a mismatch), and the rule is
  stated on the task board (template p1.2).
- **Board notes** (ratified from acceptance-run evidence, second finding):
  TASK_ATTEMPT messages are the universe's outcome message plus a
  deterministic runner suffix cross-referencing each produced non-slag
  compound to the commission that pays for it ("The board notes: X fulfills
  wx-tN-i."). This joins two facts every prompt already displays separately
  — 14 observed days showed agents producing all 8 tier-1 compounds while
  filing only 2 commissions because the link went unmade, then drifting
  into social idling as income collapsed. No hidden information moves: the
  map comes from public board titles. Runner and shallow-replay verifier
  share one helper, so they cannot diverge. Template p1.3 adds a matching
  reflection nudge ("name any commission you now know how to fulfill but
  have not yet claimed").
- **The satchel world rule** (ratified 2026-08-05 after two-model canary
  evidence): every non-slag step product an agent produces joins their
  satchel permanently (first-acquired order, deduped, never consumed — a
  personal tech tree, not item storage). Experiment availability = bases ∪
  satchel ∪ earlier products of the same attempt. `Universe.attempt` gains
  `available: frozenset[str]` (engine-supplied; the universe stays
  stateless and rng-free; default `frozenset()` preserves every
  pre-satchel behavior byte-for-byte). The engine folds satchel truth from
  TASK_ATTEMPT `step_products`; the shallow-replay verifier refolds it
  with the same rule and threads it through re-verification. Perception
  gains a YOUR SATCHEL block; template p2.0 *removes* the crucible-empties
  chaining lesson entirely — the rule existed only to explain the
  unintuitive world it replaced. Evidence: Haiku and Sonnet both produced
  ≤3 multi-step attempts out of ~200 under per-attempt re-derivation
  (p1.5); every model tested holds the made-it-so-I-have-it intuition, so
  the world now matches it, and tier-2 collapses to tier-1 cognitive load
  while recipes stay undiscovered.
- **Resume** (ratified from the day-12 529 death): `lamarck resume RUN_DIR`
  continues an interrupted live run from its last completed day. Day-batch
  transactions guarantee a clean boundary; resume verifies the chain,
  refuses finished/foreign/mixed runs (config sha, template version, and
  persona names must match the current code — a resumed run never silently
  mixes worlds), rebuilds all engine state by folding the log, and replays
  both RNG streams' recorded consumption (one `iter_day` per completed day
  with that day's alive list; one discarded draw per LLM_CALL) so deep
  replay of the finished log re-executes as one seamless process.
  Known cosmetic gap: pre-crash `reflections_skipped` restarts at 0 (skips
  emit no event by design). The Anthropic backend additionally carries an
  outer patience loop (8 attempts, capped exponential backoff, transient
  classes only: connection/408/409/429/5xx) so overload windows shorter
  than ~12 minutes never kill a run in the first place.
- **Cloud backend** (ratified at the user's request): `model.backend =
  "anthropic"` serves generation from the Anthropic API. The "no network in
  the sim loop" rule is refined to its actual intent: *agents* may never
  initiate network actions; model inference may be local (mlx) or cloud
  (anthropic) — the hash-chained response cache remains the sole
  determinism mechanism either way, and deep replay never re-calls any
  backend. Usage in LLM_CALL payloads is the API's own billing counts, so
  qi cost equals real spend. `GenParams.seed` is recorded but inert on the
  API (no sampling seed exists); `temperature` is sent only to models that
  accept non-default sampling (e.g. Haiku 4.5), and thinking is explicitly
  disabled on adaptive-by-default tiers (e.g. Sonnet 5). Keys come from the
  environment only — never config, never logs, never payloads. Weight-level
  phases (3+) still require local models for the trainable population; the
  mlx path stays first-class.

## 9. Deviations from PLAN.md §3 (recorded, deliberate)

- `stones_delta` added to the event envelope alongside `qi_delta` (PLAN showed
  only `qi_delta`): both ledgers difftest cleanly through one mechanism.
- Package layout: PLAN §9's top-level dirs live under the `lamarck/` Python
  package (`lamarck/engine/`, …) for standard packaging; repo-level dirs
  (`configs/`, `docs/`, `runs/`, `personas/`) unchanged.
- Probability-like config values are permille integers (`stub_success_permille`)
  per the no-floats rule.
