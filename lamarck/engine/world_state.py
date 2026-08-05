"""World-state fold powering live-mode perception (Phase 1).

``WorldStateFold`` is a PURE fold over committed ``EventRecord``s: the runner
feeds every committed event through ``apply`` (in commit order) and builds
each agent's ``PerceptionView`` from the read-only queries below. The fold
holds no economics — qi/stones/alive-for-accounting live in
``lamarck.engine.ledgers``; this fold tracks the *fictional* world: who is
where, who said what, and what each agent remembers.

Event semantics (kinds not listed have no world-state effect):

  - AGENT_SPAWNED   places ``payload["agent_id"]`` at the FIRST configured
                    location (``cfg.live.locations[0]``), records the display
                    name (``payload["name"]``, falling back to the agent_id
                    when absent — the Phase-0 stub path never constructs this
                    fold), marks alive, and appends to spawn order.
  - AGENT_DIED      clears the alive flag (the body keeps its location).
  - ACTION          only three types have world effects, and ONLY when the
                    payload is not degraded (``payload.get("degraded")``
                    truthy ⇒ the slot was forfeited/degraded to REST — no
                    world effect regardless of the recorded type):
                      * ``travel``:   moves the actor to ``payload["to"]``
                                      (asserted to be a configured location —
                                      a committed non-degraded travel to an
                                      unknown location is a producer bug).
                      * ``converse``: open-air speech. Delivered AT EMISSION
                                      into the inboxes of every OTHER living
                                      agent whose location equals the
                                      speaker's; a listener who travels away
                                      later still heard it, and an agent who
                                      arrives later never does.
                      * ``note``:     appends ``payload["text"]`` to the
                                      actor's private notes.
  - REFLECTION      stores ``payload["text"]`` as the actor's latest rolling
                    self-summary (later reflections replace earlier ones).
  - TASK_ATTEMPT    appends ``payload["message"]`` to the actor's outcome
                    history (the in-fiction verifier verdict).

Query semantics (all read-only; the fold NEVER mutates on read; every list
is freshly built in a deterministic order):

  - ``location(agent)``       current location.
  - ``co_present(agent)``     display names of OTHER living agents at the
                              same location, in spawn order.
  - ``heard(agent, day)``     inbox filtered to ``utterance.day >= day - 1``
                              (today and yesterday), capped to the 6 most
                              recent, returned oldest-first as
                              ``HeardUtterance`` models.
  - ``notes(agent)``          last 5 note texts, oldest first.
  - ``reflection(agent)``     latest reflection text, ``""`` before the first.
  - ``outcomes(agent)``       last 3 task-attempt messages, oldest first.
  - ``can_trade(actor, target)``  advisory pre-emission check for the runner
                              (target exists, is alive, is co-located, is not
                              the actor), returning ``(ok, reason)``. The
                              fold never validates COMMITTED trades —
                              accounting is the ledger's job.

Assertion stance (LMK_ASSERT, always on): the fold asserts exactly what it
is contractually owed by producers of committed events — registered/alive
actors on world-affecting events, well-typed payload fields it consumes, and
travel destinations drawn from the configured locations. It never validates
model-proposed arguments (the runner's retry/degrade path owns that) and
never inspects ``seq``/``hash`` (chain integrity is the event store's job).
"""

from __future__ import annotations

from typing import NamedTuple

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import EventKind, EventRecord, HeardUtterance, LiveWorldConfig

# Perception caps, per the contracts' PerceptionView field docs.
HEARD_MAX = 6  # most recent utterances carried into a prompt
NOTES_MAX = 5  # last notes carried into a prompt
OUTCOMES_MAX = 3  # last task-attempt messages carried into a prompt


class _Utterance(NamedTuple):
    """One committed converse, as recorded at emission time."""

    day: int
    tick: int
    from_agent: str
    location: str  # the speaker's location at emission
    text: str


class WorldStateFold:
    """Pure world-state fold over committed events (see module docstring)."""

    def __init__(self, cfg: LiveWorldConfig) -> None:
        # pydantic guarantees a non-empty locations list; guard anyway since
        # locations[0] is the spawn point and a silent IndexError helps nobody.
        LMK_ASSERT(len(cfg.live.locations) > 0, "live.locations must be non-empty")
        self._known_locations = frozenset(cfg.live.locations)
        self._spawn_location = cfg.live.locations[0]
        self._location: dict[str, str] = {}
        self._name: dict[str, str] = {}
        self._alive: dict[str, bool] = {}
        self._spawn_order: list[str] = []
        self._inbox: dict[str, list[_Utterance]] = {}
        self._notes: dict[str, list[str]] = {}
        self._reflection: dict[str, str] = {}
        self._outcomes: dict[str, list[str]] = {}
        self._satchel: dict[str, list[str]] = {}
        self._satchel_seen: dict[str, set[str]] = {}  # membership only; never iterated

    # ----------------------------------------------------------------- fold

    def apply(self, ev: EventRecord) -> None:
        """Fold one committed event into the world state."""
        if ev.kind is EventKind.AGENT_SPAWNED:
            self._apply_spawn(ev)
        elif ev.kind is EventKind.AGENT_DIED:
            self._assert_registered_alive(ev.actor, ev)
            self._alive[ev.actor] = False
        elif ev.kind is EventKind.ACTION:
            self._apply_action(ev)
        elif ev.kind is EventKind.REFLECTION:
            self._assert_registered_alive(ev.actor, ev)
            self._reflection[ev.actor] = self._payload_str(ev, "text")
        elif ev.kind is EventKind.TASK_ATTEMPT:
            self._assert_registered_alive(ev.actor, ev)
            self._outcomes[ev.actor].append(self._payload_str(ev, "message"))
            # Satchel rule (2026-08-05): every non-slag step product joins the
            # maker's satchel — first-acquired order, deduped, never removed.
            products = ev.payload.get("step_products")
            LMK_ASSERT(
                isinstance(products, list),
                "TASK_ATTEMPT payload needs a step_products list",
                seq=ev.seq,
            )
            assert isinstance(products, list)  # narrow for mypy; guaranteed above
            satchel = self._satchel[ev.actor]
            seen = self._satchel_seen[ev.actor]
            for product in products:
                if isinstance(product, str) and product != "slag" and product not in seen:
                    seen.add(product)
                    satchel.append(product)
        # Every other kind (run/day/phase markers, LLM_CALL, LEDGER_ADJUST)
        # has no world-state effect.

    # -------------------------------------------------------------- queries

    def location(self, agent: str) -> str:
        """Current location of ``agent`` (dead agents keep their last one)."""
        self._assert_registered(agent)
        return self._location[agent]

    def co_present(self, agent: str) -> list[str]:
        """Display names of OTHER living agents at ``agent``'s location,
        in spawn order."""
        self._assert_registered(agent)
        here = self._location[agent]
        return [
            self._name[other]
            for other in self._spawn_order
            if other != agent and self._alive[other] and self._location[other] == here
        ]

    def heard(self, agent: str, day: int) -> list[HeardUtterance]:
        """Utterances delivered to ``agent`` with ``day >= day - 1``, the
        ``HEARD_MAX`` most recent, oldest first."""
        self._assert_registered(agent)
        windowed = [u for u in self._inbox[agent] if u.day >= day - 1]
        return [
            HeardUtterance(
                from_agent=u.from_agent,
                from_name=self._name[u.from_agent],
                text=u.text,
            )
            for u in windowed[-HEARD_MAX:]
        ]

    def notes(self, agent: str) -> list[str]:
        """The agent's last ``NOTES_MAX`` note texts, oldest first."""
        self._assert_registered(agent)
        return list(self._notes[agent][-NOTES_MAX:])

    def reflection(self, agent: str) -> str:
        """The agent's latest reflection text (``""`` before the first)."""
        self._assert_registered(agent)
        return self._reflection[agent]

    def outcomes(self, agent: str) -> list[str]:
        """The agent's last ``OUTCOMES_MAX`` task-attempt messages, oldest
        first."""
        self._assert_registered(agent)
        return list(self._outcomes[agent][-OUTCOMES_MAX:])

    def satchel(self, agent: str) -> list[str]:
        """Every non-slag compound ``agent`` has ever produced —
        first-acquired order, deduped (the personal tech tree)."""
        self._assert_registered(agent)
        return list(self._satchel[agent])

    def can_trade(self, actor: str, target: str) -> tuple[bool, str]:
        """Advisory pre-emission check: may ``actor`` trade with ``target``?

        Returns ``(True, "")`` when the target exists, is alive, is
        co-located with the actor, and is not the actor; otherwise
        ``(False, reason)`` with a deterministic, model-facing reason. The
        runner consults this BEFORE emitting a trade; the fold never
        validates committed trades (accounting is the ledger's job).
        """
        self._assert_registered(actor)
        LMK_ASSERT(self._alive[actor], "can_trade consulted for a dead actor", actor=actor)
        if target not in self._location:
            return (False, f"there is no one called {target!r}")
        if not self._alive[target]:
            return (False, f"{self._name[target]} is dead")
        if self._location[target] != self._location[actor]:
            return (False, f"{self._name[target]} is not here")
        if target == actor:
            return (False, "you cannot trade with yourself")
        return (True, "")

    # ------------------------------------------------------------ internals

    def _apply_spawn(self, ev: EventRecord) -> None:
        agent_id = ev.payload.get("agent_id")
        LMK_ASSERT(
            isinstance(agent_id, str) and agent_id != "",
            "AGENT_SPAWNED payload needs a non-empty str agent_id",
            payload=ev.payload,
            seq=ev.seq,
        )
        assert isinstance(agent_id, str)  # narrow for mypy; guaranteed above
        LMK_ASSERT(
            agent_id not in self._location,
            "agent spawned twice",
            agent=agent_id,
            seq=ev.seq,
        )
        name = ev.payload.get("name", agent_id)
        LMK_ASSERT(
            isinstance(name, str) and name != "",
            "AGENT_SPAWNED name must be a non-empty str when present",
            payload=ev.payload,
            seq=ev.seq,
        )
        assert isinstance(name, str)
        self._location[agent_id] = self._spawn_location
        self._name[agent_id] = name
        self._alive[agent_id] = True
        self._spawn_order.append(agent_id)
        self._inbox[agent_id] = []
        self._notes[agent_id] = []
        self._reflection[agent_id] = ""
        self._outcomes[agent_id] = []
        self._satchel[agent_id] = []
        self._satchel_seen[agent_id] = set()

    def _apply_action(self, ev: EventRecord) -> None:
        if ev.payload.get("degraded"):
            return  # degraded/forfeited slots have no world effect
        action_type = ev.payload.get("type")
        if action_type == "travel":
            self._assert_registered_alive(ev.actor, ev)
            to = ev.payload.get("to")
            LMK_ASSERT(
                isinstance(to, str) and to in self._known_locations,
                "non-degraded travel to an unknown location (producer bug)",
                to=to,
                actor=ev.actor,
                seq=ev.seq,
            )
            assert isinstance(to, str)
            self._location[ev.actor] = to
        elif action_type == "converse":
            self._assert_registered_alive(ev.actor, ev)
            text = self._payload_str(ev, "text")
            here = self._location[ev.actor]
            utterance = _Utterance(
                day=ev.day, tick=ev.tick, from_agent=ev.actor, location=here, text=text
            )
            for listener in self._spawn_order:
                if (
                    listener != ev.actor
                    and self._alive[listener]
                    and self._location[listener] == here
                ):
                    self._inbox[listener].append(utterance)
        elif action_type == "note":
            self._assert_registered_alive(ev.actor, ev)
            self._notes[ev.actor].append(self._payload_str(ev, "text"))
        # Every other action type has no world-state effect.

    def _payload_str(self, ev: EventRecord, key: str) -> str:
        value = ev.payload.get(key)
        LMK_ASSERT(
            isinstance(value, str),
            f"{ev.kind} payload needs a str {key!r}",
            payload=ev.payload,
            seq=ev.seq,
        )
        assert isinstance(value, str)
        return value

    def _assert_registered(self, agent: str) -> None:
        LMK_ASSERT(
            agent in self._location,
            "world-state query/event for an unregistered agent",
            agent=agent,
        )

    def _assert_registered_alive(self, agent: str, ev: EventRecord) -> None:
        LMK_ASSERT(
            agent in self._location,
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
