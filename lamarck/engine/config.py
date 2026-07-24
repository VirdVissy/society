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

``load_live_config`` (Phase 1) is the same pipeline for the extended
``LiveWorldConfig``: every Phase-0 validation above runs unchanged, plus
engine-owned checks on the live sections:

  4. ``live.locations`` must be non-empty (pydantic ``min_length=1``) with
     unique, non-empty-string entries.
  5. ``model.backend`` must be one of ``{"mlx", "scripted"}``.
  6. ``universe.seed`` must be a hex u64, exactly like ``world.master_seed``
     (the two seeds are independent by design; the format rule is shared).
  7. ``live.first_discovery_multiplier >= 1`` is enforced by the contracts
     model itself (pydantic ``Field(ge=1)``) — a 0 surfaces as
     ``ValidationError``.

``live_config_sha`` fingerprints the FULL extended model; the Phase-0
``config_sha`` of a stub config is untouched by Phase 1 (pinned in tests).

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
from collections import Counter
from pathlib import Path
from typing import Any

from lamarck.contracts import ActionType, LiveWorldConfig, WorldConfig

_U64_BOUND = 1 << 64
_LIVE_BACKENDS = frozenset({"mlx", "scripted"})


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


def _check_hex_u64(raw: str, field: str) -> None:
    """Require ``raw`` to parse as a hex integer inside the u64 range.

    Shared by every seed-shaped config field; error messages name ``field``
    (kept byte-identical to the original master_seed messages)."""
    try:
        seed = int(raw, 16)
    except ValueError:
        raise ValueError(f"{field} is not a hex integer: {raw!r}") from None
    if not 0 <= seed < _U64_BOUND:
        raise ValueError(f"{field} outside u64 range [0, 2**64): {raw!r}")


def _check_master_seed(cfg: WorldConfig) -> None:
    """Require world.master_seed to be a hex u64."""
    _check_hex_u64(cfg.world.master_seed, "world.master_seed")


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


def _check_live_sections(cfg: LiveWorldConfig) -> None:
    """Engine-owned validation of the Phase-1 sections (see module docstring).

    Bounds already guaranteed by the contracts model (non-empty locations
    list, ``first_discovery_multiplier >= 1``, permille/token ranges) are NOT
    re-checked here — pydantic raises first and names the field."""
    empty_at = [i for i, loc in enumerate(cfg.live.locations) if loc == ""]
    if empty_at:
        raise ValueError(f"live.locations contains empty-string entries at indices {empty_at}")
    dupes = sorted(loc for loc, n in Counter(cfg.live.locations).items() if n > 1)
    if dupes:
        raise ValueError(f"live.locations contains duplicate entries: {dupes}")
    if cfg.model.backend not in _LIVE_BACKENDS:
        raise ValueError(
            f"model.backend must be one of {sorted(_LIVE_BACKENDS)}: {cfg.model.backend!r}"
        )
    _check_hex_u64(cfg.universe.seed, "universe.seed")


def load_live_config(path: str | Path) -> LiveWorldConfig:
    """Load and fully validate a live (Phase-1) world config from a TOML file.

    The Phase-0 pipeline runs unchanged on the frozen base sections
    (action_costs completeness, master_seed hex-u64, permille bounds), then
    ``_check_live_sections`` validates ``[live]``/``[model]``/``[universe]``.
    Returns the frozen ``LiveWorldConfig``.
    """
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    _check_action_costs_exact(data)
    cfg = LiveWorldConfig.model_validate(data)
    _check_master_seed(cfg)
    _check_permille(cfg)
    _check_live_sections(cfg)
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


def live_config_sha(cfg: LiveWorldConfig) -> str:
    """sha256 hex fingerprint of the canonical serialization of the LIVE cfg.

    Same pattern as ``config_sha`` but over the full extended model — the
    Phase-1 run fingerprint covers ``[model]``/``[universe]``/``[live]`` too.
    Kept separate so the Phase-0 stub fingerprint stays byte-stable (its pin
    lives in tests/test_config_live.py).
    """
    from lamarck.eventstore.canonical import canonical_bytes, sha256_hex

    return sha256_hex(canonical_bytes(cfg.model_dump(mode="json")))
