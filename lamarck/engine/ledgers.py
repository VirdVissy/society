"""Live balance ledgers, the independent fold, and the ledger difftest.

The event log is ground truth; balances are a fold over it. This module keeps
that fold twice, on purpose:

  - ``Ledgers`` — the LIVE implementation the runner feeds event-by-event,
    with every Phase-0 invariant enforced via LMK_ASSERT (assertions always
    on; a violation is a bug in the producer, never user error).
  - ``fold_balances`` — a COMPLETELY INDEPENDENT re-derivation: a plain
    module-level fold sharing no code path with ``Ledgers.apply`` beyond the
    contracts models. It is deliberately unguarded and simple so the two
    implementations can disagree if either is buggy — that disagreement is
    exactly what ``assert_difftest`` exists to catch.

Event semantics applied by BOTH implementations (per the frozen contracts,
"PHASE-0 ACTION SEMANTICS" and "PHASE-1 LIVE SEMANTICS"):

  - AGENT_SPAWNED   registers ``payload["agent_id"]`` with
                    ``qi = payload["qi_max"]``, ``stones =
                    payload["starting_stones"]``, ``alive = True``. The
                    event's own deltas are zero (asserted live).
  - ACTION          applies ``qi_delta`` / ``stones_delta`` to ``ev.actor``.
  - LLM_CALL        applies ``qi_delta`` to ``ev.actor`` exactly like ACTION
                    (Phase-1 token billing: ``qi_delta = -qi_llm_cost(...)``);
                    ``stones_delta`` must be zero (asserted live) — model
                    calls never move stones.
  - LEDGER_ADJUST   same delta application (bounty credits etc.).
  - AGENT_DIED      clears the alive flag of ``ev.actor``.
  - DAY_STARTED     resets per-day qi-spend tracking (live ledger only; the
                    fold carries no spend state).
  - TASK_ATTEMPT,   balance no-ops carrying zero deltas (asserted live) whose
    REFLECTION      actor must be a registered, ALIVE agent — they are
                    world-emitted evidence/memory records, never money moves.
  - RUN_STARTED, PHASE_STARTED, RUN_FINISHED are balance no-ops.

Invariants enforced by the live ledger (LMK_ASSERT):
  - ACTION / LLM_CALL / LEDGER_ADJUST / AGENT_DIED / TASK_ATTEMPT /
    REFLECTION target a registered, ALIVE actor (strict by decision: even
    adjustments may not target the dead).
  - stones never go negative; qi MAY go negative (death is decided at dusk by
    the runner, per contracts — the ledger only accounts).
  - world events (and spawns and deaths) carry zero deltas; so do
    TASK_ATTEMPT and REFLECTION; LLM_CALL carries zero ``stones_delta``.
  - an agent's qi spend within one day (the negative qi_delta of its ACTION
    *and* LLM_CALL events since the last DAY_STARTED — Phase-1 extension per
    the contracts' live semantics) never exceeds ``qi.daily_allowance`` — a
    committed over-allowance event means the runner failed to degrade it.
    Phase-0 histories contain no LLM_CALL events, so Phase-0 accounting is
    byte-identical to before the extension.

The ledger never inspects ``seq`` or ``hash`` — chain integrity is the event
store's concern; that separation is intentional. It also never emits events
and never decides degradation: it accounts for committed events and exposes
the allowance rules (``AllowanceMeter``) so those rules live in one place.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import EventKind, EventRecord, LedgerBalances, WorldConfig

_NOOP_KINDS = frozenset({EventKind.RUN_STARTED, EventKind.PHASE_STARTED, EventKind.RUN_FINISHED})


class AllowanceMeter:
    """Per-agent daily qi-spend meter against a fixed daily allowance.

    The single home of the allowance rule: an agent may spend at most
    ``daily_allowance`` qi per day; spending exactly the allowance is
    allowed, one more is not. ``can_spend`` is the non-throwing query the
    runner consults BEFORE emitting an action (degrading to REST on False);
    ``note_spend`` records a committed spend and asserts the cap, because a
    recorded over-allowance spend is always a producer bug.

    ``Ledgers`` owns a meter and feeds it from committed ACTION events — when
    a meter is ledger-owned, do NOT also call ``note_spend`` manually or the
    spend double-counts.
    """

    def __init__(self, daily_allowance: int) -> None:
        LMK_ASSERT(daily_allowance >= 1, "daily_allowance must be >= 1", got=daily_allowance)
        self._daily_allowance = daily_allowance
        self._spent: dict[str, int] = {}

    def spent(self, agent: str) -> int:
        """Qi spent by ``agent`` since the last ``reset_day`` (0 if none)."""
        return self._spent.get(agent, 0)

    def remaining(self, agent: str) -> int:
        """Allowance left today for ``agent``; never negative (cap asserted)."""
        return self._daily_allowance - self.spent(agent)

    def can_spend(self, agent: str, amount: int) -> bool:
        """True iff spending ``amount`` more qi today stays within allowance."""
        LMK_ASSERT(amount >= 0, "spend amount must be non-negative", agent=agent, amount=amount)
        return self.spent(agent) + amount <= self._daily_allowance

    def note_spend(self, agent: str, amount: int) -> None:
        """Record a committed spend; asserts the daily cap is respected."""
        LMK_ASSERT(amount >= 0, "spend amount must be non-negative", agent=agent, amount=amount)
        new_total = self.spent(agent) + amount
        LMK_ASSERT(
            new_total <= self._daily_allowance,
            "daily qi allowance exceeded",
            agent=agent,
            spent=new_total,
            allowance=self._daily_allowance,
        )
        self._spent[agent] = new_total

    def reset_day(self) -> None:
        """Zero every agent's spend (called on DAY_STARTED)."""
        self._spent.clear()


class Ledgers:
    """Live qi/stone/alive balances (implements ``LedgersP``).

    Every mutation arrives as an already-committed ``EventRecord`` via
    ``apply``; see the module docstring for the exact per-kind semantics and
    invariants. Per-day spend tracking lives in the owned ``allowance``
    meter, which the runner also consults for pre-emission checks.
    """

    def __init__(self, cfg: WorldConfig) -> None:
        self._qi: dict[str, int] = {}
        self._stones: dict[str, int] = {}
        self._alive: dict[str, bool] = {}
        self._allowance = AllowanceMeter(cfg.qi.daily_allowance)

    @property
    def allowance(self) -> AllowanceMeter:
        """The ledger-owned allowance meter (fed by ``apply``; read-only use)."""
        return self._allowance

    def apply(self, ev: EventRecord) -> None:
        """Fold one committed event into the live balances."""
        if ev.kind is EventKind.AGENT_SPAWNED:
            self._apply_spawn(ev)
        elif ev.kind is EventKind.ACTION:
            self._apply_deltas(ev)
            self._note_qi_spend(ev)
        elif ev.kind is EventKind.LLM_CALL:
            LMK_ASSERT(
                ev.stones_delta == 0,
                "LLM_CALL must carry zero stones_delta",
                actor=ev.actor,
                stones_delta=ev.stones_delta,
                seq=ev.seq,
            )
            self._apply_deltas(ev)
            self._note_qi_spend(ev)
        elif ev.kind is EventKind.LEDGER_ADJUST:
            self._apply_deltas(ev)
        elif ev.kind is EventKind.TASK_ATTEMPT or ev.kind is EventKind.REFLECTION:
            self._assert_zero_deltas(ev)
            self._assert_registered_alive(ev.actor, ev)
        elif ev.kind is EventKind.AGENT_DIED:
            self._assert_zero_deltas(ev)
            self._assert_registered_alive(ev.actor, ev)
            self._alive[ev.actor] = False
        elif ev.kind is EventKind.DAY_STARTED:
            self._assert_zero_deltas(ev)
            self._allowance.reset_day()
        elif ev.kind in _NOOP_KINDS:
            self._assert_zero_deltas(ev)
        else:  # pragma: no cover - EventKind is closed; new kinds must be wired here
            LMK_ASSERT(False, "unhandled event kind", kind=str(ev.kind), seq=ev.seq)

    def balances(self) -> LedgerBalances:
        """Snapshot of the live balances (copies; never aliases internal state)."""
        return LedgerBalances(qi=dict(self._qi), stones=dict(self._stones), alive=dict(self._alive))

    # ------------------------------------------------------------- internals

    def _apply_spawn(self, ev: EventRecord) -> None:
        self._assert_zero_deltas(ev)
        agent_id = ev.payload.get("agent_id")
        qi_max = ev.payload.get("qi_max")
        starting_stones = ev.payload.get("starting_stones")
        LMK_ASSERT(
            isinstance(agent_id, str) and agent_id != "",
            "AGENT_SPAWNED payload needs a non-empty str agent_id",
            payload=ev.payload,
            seq=ev.seq,
        )
        assert isinstance(agent_id, str)  # narrow for mypy; guaranteed above
        LMK_ASSERT(
            isinstance(qi_max, int) and not isinstance(qi_max, bool) and qi_max >= 0,
            "AGENT_SPAWNED payload needs int qi_max >= 0",
            payload=ev.payload,
            seq=ev.seq,
        )
        assert isinstance(qi_max, int)
        LMK_ASSERT(
            isinstance(starting_stones, int)
            and not isinstance(starting_stones, bool)
            and starting_stones >= 0,
            "AGENT_SPAWNED payload needs int starting_stones >= 0",
            payload=ev.payload,
            seq=ev.seq,
        )
        assert isinstance(starting_stones, int)
        LMK_ASSERT(
            agent_id not in self._alive,
            "agent spawned twice",
            agent=agent_id,
            seq=ev.seq,
        )
        self._qi[agent_id] = qi_max
        self._stones[agent_id] = starting_stones
        self._alive[agent_id] = True

    def _apply_deltas(self, ev: EventRecord) -> None:
        """Apply qi/stones deltas to ``ev.actor``; validates before mutating."""
        self._assert_registered_alive(ev.actor, ev)
        new_stones = self._stones[ev.actor] + ev.stones_delta
        LMK_ASSERT(
            new_stones >= 0,
            "stones balance would go negative",
            agent=ev.actor,
            balance=self._stones[ev.actor],
            delta=ev.stones_delta,
            seq=ev.seq,
        )
        self._qi[ev.actor] += ev.qi_delta  # qi MAY go negative; dusk decides death
        self._stones[ev.actor] = new_stones

    def _note_qi_spend(self, ev: EventRecord) -> None:
        """Feed the allowance meter: negative qi on ACTION / LLM_CALL is the
        day's spend (contracts, live semantics: both kinds count)."""
        spend = -ev.qi_delta if ev.qi_delta < 0 else 0
        self._allowance.note_spend(ev.actor, spend)

    def _assert_registered_alive(self, agent: str, ev: EventRecord) -> None:
        LMK_ASSERT(
            agent in self._alive,
            "event targets an unregistered agent",
            agent=agent,
            kind=str(ev.kind),
            seq=ev.seq,
        )
        LMK_ASSERT(
            self._alive[agent],
            "event targets a dead agent",
            agent=agent,
            kind=str(ev.kind),
            seq=ev.seq,
        )

    def _assert_zero_deltas(self, ev: EventRecord) -> None:
        LMK_ASSERT(
            ev.qi_delta == 0 and ev.stones_delta == 0,
            "event kind must carry zero deltas",
            kind=str(ev.kind),
            qi_delta=ev.qi_delta,
            stones_delta=ev.stones_delta,
            seq=ev.seq,
        )


def fold_balances(events: Iterable[EventRecord], cfg: WorldConfig) -> LedgerBalances:
    """Independently re-derive final balances by folding ``events``.

    This is the difftest's second opinion: a straightforward, unguarded fold
    that shares no code with ``Ledgers.apply``. Keep it boring. ``cfg`` is
    part of the frozen contract signature; Phase-0 spawn events carry their
    own qi_max/starting_stones, so it is currently unused (reserved for
    later phases).
    """
    qi: dict[str, int] = {}
    stones: dict[str, int] = {}
    alive: dict[str, bool] = {}
    for ev in events:
        if ev.kind is EventKind.AGENT_SPAWNED:
            agent = ev.payload["agent_id"]
            qi[agent] = ev.payload["qi_max"]
            stones[agent] = ev.payload["starting_stones"]
            alive[agent] = True
        elif (
            ev.kind is EventKind.ACTION
            or ev.kind is EventKind.LLM_CALL
            or ev.kind is EventKind.LEDGER_ADJUST
        ):
            qi[ev.actor] += ev.qi_delta
            stones[ev.actor] += ev.stones_delta
        elif ev.kind is EventKind.AGENT_DIED:
            alive[ev.actor] = False
    return LedgerBalances(qi=qi, stones=stones, alive=alive)


def _diff_lines(
    field: str, live_map: Mapping[str, object], fold_map: Mapping[str, object]
) -> list[str]:
    lines: list[str] = []
    for agent in sorted(set(live_map) | set(fold_map)):
        lv = live_map.get(agent, "<absent>")
        fv = fold_map.get(agent, "<absent>")
        if lv != fv:
            lines.append(f"{field}[{agent!r}]: live={lv!r} fold={fv!r}")
    return lines


def assert_difftest(live: Ledgers, events: Iterable[EventRecord], cfg: WorldConfig) -> None:
    """LMK_ASSERT that the independent fold of ``events`` equals ``live.balances()``.

    On mismatch the failure message names every disagreeing agent with its
    live and fold values per field — the postmortem starts from the message.
    """
    fold = fold_balances(events, cfg)
    live_b = live.balances()
    if fold == live_b:
        return
    lines = (
        _diff_lines("qi", live_b.qi, fold.qi)
        + _diff_lines("stones", live_b.stones, fold.stones)
        + _diff_lines("alive", live_b.alive, fold.alive)
    )
    LMK_ASSERT(
        False,
        "ledger difftest failed: live != fold | " + "; ".join(lines),
    )
