"""Day scheduler (contracts: ``SchedulerP``).

A day is a fixed sequence of ``TickSlot`` tuples ``(phase, round, tick, actor)``:

  1. one DAWN world slot,
  2. ``rounds_per_day`` rounds of per-agent ACTION slots,
  3. one DUSK world slot, then one NIGHT world slot (always the day's last).

Tick indices are contiguous from 0 across the whole day: dawn is tick 0,
actions follow, dusk is next, night is last. World slots carry
``actor == WORLD_ACTOR`` and ``round == 0`` — the round field is meaningful
only on ACTION slots (the DAWN slot is contractually pinned as
``(DAWN, 0, 0, "")``; DUSK and NIGHT follow the same world-slot rule).

Within round ``r``, every alive agent receives ``ticks_per_agent_per_round``
consecutive ACTION slots; the agent order is a seeded shuffle of a COPY of
``alive_ids``, drawn from the ``"scheduler"`` stream, reshuffled once per
round (exactly one shuffle per round, regardless of ticks-per-agent).

Determinism:
  - ``iter_day`` computes the whole day eagerly at call time — the
    ``"scheduler"`` stream is consumed by exactly ``rounds_per_day`` shuffles
    per call, independent of how far the returned iterator is advanced.
  - The stream is STATEFUL ACROSS DAYS by design: it is one sequence for the
    whole run, never reseeded, so day N's orders depend on the calls made for
    days < N. Two schedulers over fresh ``RngStreams`` with the same master
    seed yield identical slot sequences for the same ``(day, alive_ids)``
    call sequence — that is the replay contract.
  - ``alive_ids`` order matters (it is the shuffle's input); callers must
    pass it in a canonical order (the runner uses spawn order).
"""

from __future__ import annotations

from collections.abc import Iterator

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import WORLD_ACTOR, DayPhase, TickSlot, WorldConfig
from lamarck.engine.rng import RngStreams

SCHEDULER_STREAM = "scheduler"

_WORLD_ROUND = 0  # round field carried by DAWN/DUSK/NIGHT world slots


class Scheduler:
    """Deterministic day scheduler over a config and a shared RNG-stream set.

    Implements ``SchedulerP``. Holds the memoized ``"scheduler"`` stream from
    ``rngs``; constructing the scheduler derives the stream but consumes
    nothing from it.
    """

    def __init__(self, cfg: WorldConfig, rngs: RngStreams) -> None:
        self._cfg = cfg
        self._rng = rngs.stream(SCHEDULER_STREAM)

    def total_slots_per_day(self, n_alive: int) -> int:
        """Slot count iter_day yields for ``n_alive`` agents: 3 world slots
        (dawn, dusk, night) + rounds * agents * ticks-per-agent action slots."""
        LMK_ASSERT(n_alive >= 0, "n_alive must be non-negative", n_alive=n_alive)
        world = self._cfg.world
        return 3 + world.rounds_per_day * n_alive * world.ticks_per_agent_per_round

    def iter_day(self, day: int, alive_ids: list[str]) -> Iterator[TickSlot]:
        """Yield day ``day``'s slots in order (see module docstring for the spec).

        The full sequence is computed (and the scheduler stream consumed) at
        call time; the returned iterator walks a precomputed list.
        """
        LMK_ASSERT(day >= 0, "day must be non-negative", day=day)
        LMK_ASSERT(
            len(set(alive_ids)) == len(alive_ids),
            "alive_ids contains duplicates",
            alive_ids=alive_ids,
        )
        LMK_ASSERT(
            WORLD_ACTOR not in alive_ids,
            "alive_ids may not contain the world actor",
            alive_ids=alive_ids,
        )

        world = self._cfg.world
        slots: list[TickSlot] = []
        tick = 0

        slots.append((DayPhase.DAWN, _WORLD_ROUND, tick, WORLD_ACTOR))
        tick += 1

        for rnd in range(world.rounds_per_day):
            order = list(alive_ids)  # shuffle a copy; caller's list is never mutated
            self._rng.shuffle(order)  # exactly one shuffle per round
            for agent in order:
                for _ in range(world.ticks_per_agent_per_round):
                    slots.append((DayPhase.ACTION, rnd, tick, agent))
                    tick += 1

        slots.append((DayPhase.DUSK, _WORLD_ROUND, tick, WORLD_ACTOR))
        tick += 1
        slots.append((DayPhase.NIGHT, _WORLD_ROUND, tick, WORLD_ACTOR))
        tick += 1

        LMK_ASSERT(
            tick == self.total_slots_per_day(len(alive_ids)),
            "slot count mismatch",
            got=tick,
            expected=self.total_slots_per_day(len(alive_ids)),
        )
        return iter(slots)
