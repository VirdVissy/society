"""lamarck.engine — config loading, RNG substreams, day scheduler, ledgers,
and the Phase-1 world-state fold.

Public API of the engine subsystem. Everything here implements the frozen
protocols in ``lamarck.contracts``; see each module's docstring for its spec.
"""

from lamarck.engine.clock import SCHEDULER_STREAM, Scheduler
from lamarck.engine.config import (
    config_sha,
    live_config_sha,
    load_live_config,
    load_world_config,
)
from lamarck.engine.ledgers import (
    AllowanceMeter,
    Ledgers,
    assert_difftest,
    fold_balances,
)
from lamarck.engine.rng import RngStreams, splitmix64
from lamarck.engine.world_state import WorldStateFold

__all__ = [
    "SCHEDULER_STREAM",
    "AllowanceMeter",
    "Ledgers",
    "RngStreams",
    "Scheduler",
    "WorldStateFold",
    "assert_difftest",
    "config_sha",
    "fold_balances",
    "live_config_sha",
    "load_live_config",
    "load_world_config",
    "splitmix64",
]
