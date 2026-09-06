# lamarck

**A discovery engine whose searchers are mortal, teachable, and watchable.**

A persistent society of LLM agents in a cultivation-world setting: lifespans
denominated in tokens, skills that exist only when verified, knowledge that
spreads only by teaching, experience consolidated into per-agent LoRA weights
during sleep, reproduction by adapter merging — all of it event-sourced,
hash-chained, and replayable, with full belief/technique provenance
("mind-git").

Read [PLAN.md](PLAN.md) — the project constitution: thesis, prior-art ledger,
full specifications, phase plan, pre-registered experiments, risk register.

Status: **Phase 1 closed** (`v0.2.0`, 2026-08-21 — 8 founders, 30 unattended
sim-days on Haiku 4.5, 24 distinct verified discoveries, byte-identical deep
replay; devlog 002). **Phase 2 — Lineages** is in progress against
[docs/plans/phase-2-implementation.md](docs/plans/phase-2-implementation.md).
Ratified deviations from PLAN.md live in [docs/SPEC.md](docs/SPEC.md) §9/§9a.

## Development

```sh
uv sync                              # base + dev deps: what CI runs (model-free)
uv sync --extra llm --extra api      # + mlx-lm (local models) and anthropic (cloud)
uv run --no-sync pytest              # --no-sync keeps the extras installed
uv run --no-sync ruff check . && uv run --no-sync ruff format --check . && uv run --no-sync mypy
```

A plain `uv sync` / `uv run` (without `--no-sync`) removes both extras
again, so every model-backed launch uses `uv run --no-sync`. Paid runs need
`ANTHROPIC_API_KEY` in the environment and are launched only on an explicit
go — `lamarck live` has no cost guard of its own.

The package version string stays `0.1.0.dev0` on purpose: it is hashed into
every run's RUN_STARTED event and deep replay re-emits it from the running
code, so bumping it would break byte-identical replay of existing logs.
Release identity is the git tag (`v0.X.0` at each phase close; see
CHANGELOG.md).

Conventions: assertions always on (`LMK_ASSERT`), canonical JSON is the only
serialized form, floats never touch state, seeds from the `0xDE5EED…` family,
measured claims only. Every phase closes with a tag and a devlog in
`docs/devlog/`.
