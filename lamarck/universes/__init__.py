"""lamarck.universes — problem domains with instant, deterministic verifiers.

A universe implements ``lamarck.contracts.UniverseP``: a manifest, a public
task board, a pure ``attempt`` verifier, and an operator-side
``oracle_audit``. Hidden rules never appear on any public surface.

Universe #1 (Phase 1): wuxing alchemy — ``lamarck.universes.wuxing``.
Operator audit + CLI: ``lamarck.universes.audit``
(``python -m lamarck.universes.audit configs/valley.toml``).

The audit re-exports are lazy (PEP 562) so that running the audit module as
a script does not re-import it during package init (runpy double-import
warning); ``TYPE_CHECKING`` keeps the names fully typed for mypy.
"""

from typing import TYPE_CHECKING, Any

from lamarck.universes.wuxing import WuxingUniverse

if TYPE_CHECKING:
    from lamarck.universes.audit import audit_universe, load_live_config

__all__ = [
    "WuxingUniverse",
    "audit_universe",
    "load_live_config",
]

_LAZY_AUDIT_EXPORTS = frozenset({"audit_universe", "load_live_config"})


def __getattr__(name: str) -> Any:
    if name in _LAZY_AUDIT_EXPORTS:
        from lamarck.universes import audit

        return getattr(audit, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
