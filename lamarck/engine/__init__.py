"""lamarck.engine — config loading, RNG substreams, day scheduler, ledgers.

Public API of the Phase-0 engine subsystem. Everything here implements the
frozen protocols in ``lamarck.contracts``; see each module's docstring for
its spec.
"""

from lamarck.engine.clock import SCHEDULER_STREAM, Scheduler
from lamarck.engine.config import config_sha, load_world_config
from lamarck.engine.ledgers import (
    AllowanceMeter,
    Ledgers,
    assert_difftest,
    fold_balances,
)
from lamarck.engine.rng import RngStreams, splitmix64

__all__ = [
    "SCHEDULER_STREAM",
    "AllowanceMeter",
    "Ledgers",
    "RngStreams",
    "Scheduler",
    "assert_difftest",
    "config_sha",
    "fold_balances",
    "load_world_config",
    "splitmix64",
]
