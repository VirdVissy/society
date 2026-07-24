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

Status: **Phase 0 — Foundations** (deterministic engine core, no LLM yet).

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run mypy
```

Conventions: assertions always on (`LMK_ASSERT`), canonical JSON is the only
serialized form, floats never touch state, seeds from the `0xDE5EED…` family,
measured claims only. Every phase closes with a tag and a devlog in
`docs/devlog/`.
