# Changelog

Phase closes are tagged `v0.X.0`; each has a devlog in `docs/devlog/` with the
pinned numbers. The package version string is deliberately not bumped (it is
hashed into every run's RUN_STARTED; see README).

## Unreleased

- ci: the two wall-clock test budgets are widened 3× when `CI` is set — the
  ubuntu-latest leg had been red since 2026-08-05 on shared-runner speed alone.
- docs: post-close status review (2026-09-06) — devlog 002 errata (qi
  calibration, canary cost, CI line, the unrecorded third acceptance
  attempt); SPEC §9a back-filled (template p2.3 and config-sha pins,
  auto-claim, free tier-1 materials, trade-poverty retry, template lineage,
  live report layout); Phase-2 plan §2.2 lifespan calibration corrected
  from acceptance-run evidence (`qi_max` ≈ 200–250k, not 40–60k) with the
  Stage-E canary rungs and budget amended; README/PLAN status lines.
- tests: `configs/valley-cloud.toml` fingerprint pinned.

## v0.2.0 — Phase 1: The Valley Awakens (2026-08-21)

- Minds in the loop: wuxing hidden-chemistry universe with `oracle_audit`,
  persona-driven cognition loop, strict action protocol with one billed
  retry, qi billed from real token usage (`ceil(in/4) + out`).
- Backends: mlx (local Qwen3-4B) and Anthropic cloud (Haiku 4.5 acceptance
  tier), both behind the hash-chained response cache; deep replay never
  re-calls a backend.
- World rules ratified from paid-run evidence (SPEC §9a): once-per-cultivator
  bounties, board notes, free tier-1 materials, the satchel, auto-claim, the
  satchel-ladder line, the lab journal.
- Infrastructure: `lamarck resume` (day-batch boundaries, RNG replay),
  API patience loop, simultaneous rounds (concurrency-invariant waves,
  ~5× wall-clock), report generator, dashboard.
- Acceptance: run `aea04d1a8e6f` — 30 unattended sim-days, 8 founders,
  24 distinct verified discoveries (tiers 8/8/6/1/1), 7,392 events, deep
  replay byte-identical (head `7391/edf85fbc447f`), ≈$20.4.

## v0.1.0 — Phase 0: Foundations (2026-07-23)

- Deterministic event-sourced substrate: canonical JSON, SQLite hash chain,
  config fingerprints, SplitMix64 named RNG streams, clock/scheduler, qi and
  stone ledgers, difftest, stub agents and universe, `lamarck run|replay|report`.
- Gate: 1,000-day / 8-agent stub sim in 2.1 s (< 5 s); golden run
  `903276a6edd8` (2,161 events) pinned; 146 tests; CI on ubuntu + macOS.
