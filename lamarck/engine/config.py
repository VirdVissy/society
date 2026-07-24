"""World-config loading and fingerprinting (Phase 0).

``load_world_config`` parses a TOML file (stdlib ``tomllib``) into the frozen
``WorldConfig`` pydantic model and layers three engine-owned validations on
top of what pydantic enforces:

  1. ``[qi.action_costs]`` must contain EVERY ``ActionType`` exactly once —
     no missing keys, no extras. Violations raise ``ValueError`` naming the
     offending keys.
  2. ``world.master_seed`` must parse as a hex integer (``int(s, 16)``,
     ``0x`` prefix allowed) and lie in the u64 range ``[0, 2**64)`` — the RNG
     contract consumes it as a u64. A bad seed in a config file is user
     error, hence ``ValueError`` here rather than an assertion later.
  3. Permille values (``economy.stub_success_permille``) must lie in
     ``[0, 1000]`` inclusive.

Structural problems (missing sections, wrong types) surface as pydantic
``ValidationError``; a malformed TOML file surfaces as
``tomllib.TOMLDecodeError``; a missing file as ``FileNotFoundError``.

``config_sha`` is the run-fingerprint seam: sha256 over the canonical
serialization of ``cfg.model_dump(mode="json")``. Canonical encoding is
owned by ``lamarck.eventstore.canonical`` (built concurrently to a fixed
contract: ``canonical_bytes(value) -> bytes`` rejecting floats, and
``sha256_hex(data) -> str``); this module imports those two names lazily so
the loader works even before the eventstore package lands.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from lamarck.contracts import ActionType, WorldConfig

_U64_BOUND = 1 << 64


def _check_action_costs_exact(data: dict[str, Any]) -> None:
    """Require [qi.action_costs] to cover ActionType exactly (no gaps, no extras).

    Runs on the raw TOML dict so the error can name string keys precisely;
    skipped (deferring to pydantic's structural error) when the path
    qi.action_costs is absent or not a table.
    """
    qi = data.get("qi")
    if not isinstance(qi, dict):
        return
    costs = qi.get("action_costs")
    if not isinstance(costs, dict):
        return
    expected = {a.value for a in ActionType}
    present = set(costs)
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing keys: {missing}")
        if extra:
            parts.append(f"unexpected keys: {extra}")
        raise ValueError(
            "[qi.action_costs] must contain every ActionType exactly; " + "; ".join(parts)
        )


def _check_master_seed(cfg: WorldConfig) -> None:
    """Require world.master_seed to be a hex u64."""
    raw = cfg.world.master_seed
    try:
        seed = int(raw, 16)
    except ValueError:
        raise ValueError(f"world.master_seed is not a hex integer: {raw!r}") from None
    if not 0 <= seed < _U64_BOUND:
        raise ValueError(f"world.master_seed outside u64 range [0, 2**64): {raw!r}")


def _check_permille(cfg: WorldConfig) -> None:
    """Require every permille value to lie in [0, 1000]."""
    for i, v in enumerate(cfg.economy.stub_success_permille):
        if not 0 <= v <= 1000:
            raise ValueError(f"economy.stub_success_permille[{i}] outside [0, 1000]: {v}")


def load_world_config(path: str | Path) -> WorldConfig:
    """Load and fully validate a world config from a TOML file.

    Returns the frozen ``WorldConfig``. TOML string keys of
    ``[qi.action_costs]`` are coerced to ``ActionType`` by pydantic
    (verified against the canonical ``configs/world.toml`` in tests).
    """
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    _check_action_costs_exact(data)
    cfg = WorldConfig.model_validate(data)
    _check_master_seed(cfg)
    _check_permille(cfg)
    return cfg


def config_sha(cfg: WorldConfig) -> str:
    """sha256 hex fingerprint of the canonical serialization of ``cfg``.

    Delegates canonical encoding to ``lamarck.eventstore.canonical``
    (the one true serialized form; never reimplemented here). The import is
    lazy: it is the integration seam with the concurrently-built eventstore
    package, and the loader above must work without it. ``WorldConfig``
    contains no floats (fractions are permille ints by house rule), so
    ``canonical_bytes`` is total over ``model_dump(mode="json")``.
    """
    from lamarck.eventstore.canonical import canonical_bytes, sha256_hex

    return sha256_hex(canonical_bytes(cfg.model_dump(mode="json")))
