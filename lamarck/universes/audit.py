"""Operator-side universe audit: structure plus budget feasibility.

``audit_universe(cfg)`` builds the universe named by ``cfg.universe`` and
extends its structure-only ``oracle_audit`` report with budget-feasibility
notes cross-checked against ``cfg.economy`` (materials, bounties, starting
stones) and ``cfg.live.first_discovery_multiplier``. ``ok`` remains the
structural verdict (unreachable compounds / name collisions / empty tiers);
budget findings are notes — the operator reads them before burning a night.

``main`` wires this to ``python -m lamarck.universes.audit <config.toml>``:
prints a readable report and exits 0/1 by ``report.ok`` (typer CLI wiring
arrives in wave 2). Config problems are user error → readable message,
exit 1; engine invariants stay ``LMK_ASSERT``.

The report never prints recipes — the audit surface reveals counts, minimal
derivation sizes, and budget arithmetic only.
"""

from __future__ import annotations

import sys
import tomllib
from collections import Counter

from pydantic import ValidationError

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import AuditReport, LiveWorldConfig

# The engine's loader is the one true live-config entry point (full
# validation: action-cost completeness, seeds, locations, backend). The
# audit module re-exports it so `lamarck.universes.load_live_config`
# resolves to the engine implementation (integration dedupe: this module
# briefly carried its own minimal loader while the engine's was being
# built in a parallel wave).
from lamarck.engine.config import load_live_config
from lamarck.universes.wuxing import MAX_STEPS, UNIVERSE_NAME, WuxingUniverse

__all__ = ["audit_universe", "load_live_config", "main"]


def audit_universe(cfg: LiveWorldConfig) -> AuditReport:
    """Build the configured universe and audit structure plus budgets.

    Raises ``ValueError`` for configs this auditor cannot handle (unknown
    universe name, non-v1 tier count) — user error, not an engine bug.
    """
    if cfg.universe.name != UNIVERSE_NAME:
        raise ValueError(
            f"unknown universe {cfg.universe.name!r}; only {UNIVERSE_NAME!r} is auditable in v1"
        )
    if cfg.universe.tiers != 5:
        raise ValueError(f"wuxing v1 requires universe.tiers = 5, got {cfg.universe.tiers}")
    universe = WuxingUniverse(cfg.universe.seed, cfg.universe.tiers)
    base = universe.oracle_audit()
    LMK_ASSERT(
        sum(base.compounds_by_tier.values()) == universe.manifest().compounds,
        "audit tier counts disagree with manifest",
        counts=base.compounds_by_tier,
    )
    notes = list(base.notes)
    notes.extend(_budget_notes(cfg, base))
    return AuditReport(
        ok=base.ok,
        reachable_tiers=base.reachable_tiers,
        compounds_by_tier=base.compounds_by_tier,
        min_steps=base.min_steps,
        notes=notes,
    )


def _budget_notes(cfg: LiveWorldConfig, base: AuditReport) -> list[str]:
    """Budget feasibility: materials to attempt each tier once via minimal
    derivations (one experiment = one surcharge, so a derivation must fit
    the step cap) vs founder starting stones + bounty income."""
    materials = cfg.economy.materials
    bounties = cfg.economy.bounties
    start = cfg.economy.starting_stones
    mult = cfg.live.first_discovery_multiplier
    total = sum(materials)
    notes = [
        f"budget: materials to attempt each tier once: {materials} (total {total} stones)",
        f"budget: founders start with {start} stones; bounties {bounties}; "
        f"first-in-world discovery pays {mult}x",
        f"budget: all five attempts ({total} stones) vs starting stones + one tier-1 "
        f"bounty ({start + bounties[0]}; {start + bounties[0] * mult} if first-in-world)",
    ]
    stones = start
    stalled_tier = 0
    for tier in range(1, 6):
        cost = materials[tier - 1]
        if stones < cost:
            stalled_tier = tier
            break
        stones = stones - cost + bounties[tier - 1]
    if stalled_tier:
        notes.append(
            f"budget: ladder stalls at tier {stalled_tier} "
            f"({materials[stalled_tier - 1]} stones needed, {stones} on hand)"
        )
    else:
        notes.append(
            "budget: the bounty ladder (attempt tier t once, collect its bounty, ascend) "
            f"is self-funding from {start} stones; final balance {stones}"
        )
    deepest = max(base.min_steps.values()) if base.min_steps else 0
    if deepest <= MAX_STEPS:
        notes.append(
            f"budget: every minimal derivation fits one experiment "
            f"(deepest {deepest} <= cap {MAX_STEPS})"
        )
    else:
        notes.append(
            f"budget: WARNING — deepest minimal derivation ({deepest}) exceeds the "
            f"{MAX_STEPS}-step cap; those compounds cannot be produced in one experiment"
        )
    return notes


def _render(cfg: LiveWorldConfig, path: str, report: AuditReport) -> str:
    """Readable multi-line report (no recipes, ever)."""
    by_tier = " ".join(
        f"t{tier}:{count}"
        for tier, count in sorted(report.compounds_by_tier.items(), key=lambda kv: int(kv[0]))
    )
    histogram = Counter(report.min_steps.values())
    hist_txt = ", ".join(f"{steps} steps: {count}" for steps, count in sorted(histogram.items()))
    lines = [
        "wuxing universe audit",
        f"  config: {path}",
        f"  seed: {cfg.universe.seed}  tiers: {cfg.universe.tiers}",
        f"  ok: {report.ok}",
        f"  reachable tiers: {report.reachable_tiers}",
        f"  compounds by tier: {by_tier}",
        f"  min-steps histogram: {hist_txt}",
        "  notes:",
    ]
    lines.extend(f"    - {note}" for note in report.notes)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python -m lamarck.universes.audit <config.toml>`` → 0/1 by ok."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m lamarck.universes.audit <config.toml>", file=sys.stderr)
        return 2
    try:
        cfg = load_live_config(args[0])
        report = audit_universe(cfg)
    except FileNotFoundError:
        print(f"audit: config file not found: {args[0]}", file=sys.stderr)
        return 1
    except tomllib.TOMLDecodeError as exc:
        print(f"audit: malformed TOML: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"audit: invalid config: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"audit: {exc}", file=sys.stderr)
        return 1
    print(_render(cfg, args[0], report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
