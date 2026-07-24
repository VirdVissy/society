"""Phase-0 stub decision policy — a seeded die, not a mind.

``StubPolicy`` implements the frozen ``StubPolicyP`` protocol: a pure,
deterministic weighted draw over ALL 11 ``ActionType``s from the agent's own
RNG substream. The runner constructs one policy per agent and passes it the
stream ``rngs.stream(f"agent:{agent_id}")``; the policy never derives streams
itself and never touches any other stream.

Determinism contract:

- ``decide`` is a pure function of (view, own stream state). Draws happen in
  a FIXED order: first the action type, then — only when the action is
  EXPERIMENT — the tier. No draw is ever conditioned on anything outside the
  view and the stream. The view is deliberately ignored in Phase 0: the stub
  does not try to be affordable or clever, so unaffordable choices occur and
  the runner's degradation rule ("sloppiness costs") gets exercised — in the
  canonical config that is how tier-4/5 stone poverty produces degraded
  events.
- Each draw is a single ``rng.randrange(total)`` mapped through cumulative
  weights IN TABLE ORDER — integer-only, no float paths. The tables below
  are LOAD-BEARING for the golden fingerprint: reordering entries or
  changing any weight changes every downstream hash. Change only with a
  golden re-pin.

Payload args are inert Phase-0 flavor: the engine prices actions by type
alone. ``tier`` selects materials/bounty/odds for EXPERIMENT; ``target`` is
recorded verbatim and interpreted by nothing (``StubView`` carries no peer
list, so the stub always targets ``""``). Real semantics arrive in Phase 1.
Payload values are canonical scalars only (str/int).
"""

from __future__ import annotations

import random
from typing import Any

from lamarck.asserts import LMK_ASSERT, LamarckAssertionError
from lamarck.contracts import ActionType, StubPolicyP, StubView

__all__ = ["StubPolicy"]

# Action weights in contracts-enum order (percent; sum 100). Every type is
# reachable and none is rarer than 5% — a 30-day 8-agent run (1920 action
# slots) sees every type with overwhelming probability. EXPERIMENT is the
# most common so the stub-universe/bounty path gets dense coverage.
_ACTION_WEIGHTS: tuple[tuple[ActionType, int], ...] = (
    (ActionType.EXPERIMENT, 20),
    (ActionType.CONVERSE, 12),
    (ActionType.TEACH, 8),
    (ActionType.STUDY, 10),
    (ActionType.TRADE, 8),
    (ActionType.NOTE, 6),
    (ActionType.TRAVEL, 6),
    (ActionType.MEDITATE, 10),
    (ActionType.CHALLENGE, 5),
    (ActionType.ATTEMPT_BREAKTHROUGH, 5),
    (ActionType.REST, 10),
)
_ACTION_TOTAL = 100

# Tier weights (percent; sum 100): skewed low, but tiers 4 and 5 are real
# attempts (12% / 8% of experiments). Under the canonical economy their
# materials (12 / 30 stones) exceed what a 20-stone founder can always pay,
# so these picks are the intended source of stones-degraded actions.
_TIER_WEIGHTS: tuple[tuple[int, int], ...] = ((1, 40), (2, 25), (3, 15), (4, 12), (5, 8))
_TIER_TOTAL = 100

# Actions whose payload carries a "target" arg (inert in Phase 0).
_TARGETED = frozenset(
    {
        ActionType.CONVERSE,
        ActionType.TEACH,
        ActionType.STUDY,
        ActionType.TRADE,
        ActionType.CHALLENGE,
    }
)

# Import-time table validation: the weights must cover every ActionType
# exactly once, sum to the declared totals, and respect the >=2% rarity
# floor. A broken table is a bug, never user input.
LMK_ASSERT(
    [a for a, _ in _ACTION_WEIGHTS] == list(ActionType),
    "action weight table must list every ActionType exactly once, in enum order",
)
LMK_ASSERT(
    sum(w for _, w in _ACTION_WEIGHTS) == _ACTION_TOTAL,
    "action weights must sum to the declared total",
    total=_ACTION_TOTAL,
)
LMK_ASSERT(
    all(w >= 2 for _, w in _ACTION_WEIGHTS),
    "every action weight must be >= 2 (the 2% rarity floor)",
)
LMK_ASSERT(
    [t for t, _ in _TIER_WEIGHTS] == [1, 2, 3, 4, 5],
    "tier weight table must list tiers 1..5 in order",
)
LMK_ASSERT(
    sum(w for _, w in _TIER_WEIGHTS) == _TIER_TOTAL,
    "tier weights must sum to the declared total",
    total=_TIER_TOTAL,
)


def _weighted_pick[T](rng: random.Random, table: tuple[tuple[T, int], ...], total: int) -> T:
    """One integer draw mapped through cumulative weights in table order.

    Consumes exactly one ``rng.randrange(total)`` regardless of outcome —
    the fixed-consumption property the determinism contract relies on.
    """
    roll = rng.randrange(total)
    acc = 0
    for item, weight in table:
        acc += weight
        if roll < acc:
            return item
    raise LamarckAssertionError(f"weighted pick fell through | roll={roll}, total={total}")


class StubPolicy:
    """contracts.StubPolicyP implementation (structural). See module docstring.

    Holds the agent's own stream by reference; the stream's state advances
    monotonically across the run (one or two draws per ``decide``), which is
    exactly what makes two identical runs replay identically.
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def decide(self, view: StubView) -> tuple[ActionType, dict[str, Any]]:
        """Choose an action and its Phase-0 payload args.

        Draw order (fixed): action type; then tier iff EXPERIMENT. ``view``
        is accepted per the protocol and intentionally unused in Phase 0.
        Payloads: EXPERIMENT ``{"tier": 1..5}``; targeted social actions
        ``{"target": ""}``; everything else ``{}``.
        """
        action = _weighted_pick(self._rng, _ACTION_WEIGHTS, _ACTION_TOTAL)
        if action is ActionType.EXPERIMENT:
            tier = _weighted_pick(self._rng, _TIER_WEIGHTS, _TIER_TOTAL)
            return action, {"tier": tier}
        if action in _TARGETED:
            return action, {"target": ""}
        return action, {}


def _proves_protocol(policy: StubPolicy) -> StubPolicyP:
    """Compile-time (mypy) proof that StubPolicy structurally satisfies StubPolicyP."""
    return policy
