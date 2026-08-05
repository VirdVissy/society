"""Run reports — a deterministic pure fold of a finished run's event log.

``write_report(run_dir)`` reads ``events.sqlite3`` alone (never the config,
never ``llm_texts``, never the clock) and writes ``report.json`` and
``report.md``. Both artifacts are pure functions of the event log: ints,
strings and bools only, deterministic ordering everywhere (agents in spawn
order, discoveries in commit order, histogram keys sorted), so two writes
over the same log are byte-identical. Regenerating a report can never
change a fingerprint (SPEC: report.json is unhashed convenience).

report.json layout (all counts ints; no wall time anywhere):

- header: ``run_id`` / ``mode`` / ``model_id`` (RUN_STARTED payload; stub
  runs default to ``mode = "stub"``, ``model_id = ""``) and ``days``
  (RUN_FINISHED ``days_elapsed``; 0 when the log holds none).
- ``agents``: list in spawn order — ``agent_id, name, alive, qi_spent``
  (total negative qi across the agent's events), ``qi_remaining, stones``
  (final fold), ``actions`` (histogram of committed ACTION payload types —
  degraded slots count under ``rest``), ``degraded``, ``malformed``
  (degraded with reason "malformed"), ``utterances`` (non-degraded
  converse), ``notes_written`` (non-degraded note), ``tokens_in/out``.
- ``discoveries``: verified TASK_ATTEMPT rows in commit order —
  ``day, agent_id, name, task_id`` (one row per auto-claim; the claim's
  product), ``tier, first_in_world``.
- ``distinct_verified`` + ``tier_histogram`` (str tier keys, sorted) over
  DISTINCT verified task_ids (a task's tier counted once, at its first
  verification).
- ``totals``: ``llm_calls, tokens_in, tokens_out, qi_thinking`` (qi billed
  on LLM_CALL events) vs ``qi_surcharges`` (qi billed on ACTION events).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from lamarck.contracts import EventKind, EventRecord
from lamarck.eventstore import EventStore

__all__ = ["write_report"]


class _AgentStats:
    """Mutable per-agent fold state (internal)."""

    def __init__(self, agent_id: str, name: str, qi_max: int, starting_stones: int) -> None:
        self.agent_id = agent_id
        self.name = name
        self.alive = True
        self.qi = qi_max
        self.stones = starting_stones
        self.qi_spent = 0
        self.actions: Counter[str] = Counter()
        self.degraded = 0
        self.malformed = 0
        self.utterances = 0
        self.notes_written = 0
        self.tokens_in = 0
        self.tokens_out = 0

    def fold_deltas(self, ev: EventRecord) -> None:
        self.qi += ev.qi_delta
        self.stones += ev.stones_delta
        if ev.qi_delta < 0:
            self.qi_spent += -ev.qi_delta

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "alive": self.alive,
            "qi_spent": self.qi_spent,
            "qi_remaining": self.qi,
            "stones": self.stones,
            "actions": {k: self.actions[k] for k in sorted(self.actions)},
            "degraded": self.degraded,
            "malformed": self.malformed,
            "utterances": self.utterances,
            "notes_written": self.notes_written,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }


def _fold_report(events: list[EventRecord]) -> dict[str, Any]:
    """The one report fold; see the module docstring for the layout."""
    run_id = ""
    mode = "stub"
    model_id = ""
    days = 0
    spawn_order: list[str] = []
    stats: dict[str, _AgentStats] = {}
    discoveries: list[dict[str, Any]] = []
    verified_tiers: dict[str, int] = {}  # task_id -> tier, first verification only
    llm_calls = 0
    tokens_in = 0
    tokens_out = 0
    qi_thinking = 0
    qi_surcharges = 0

    for ev in events:
        if ev.kind is EventKind.RUN_STARTED:
            payload = ev.payload
            run_id = str(payload.get("run_id", ""))
            mode = str(payload.get("mode", "stub"))
            model_id = str(payload.get("model_id", ""))
        elif ev.kind is EventKind.AGENT_SPAWNED:
            agent_id = str(ev.payload.get("agent_id", ""))
            spawn_order.append(agent_id)
            stats[agent_id] = _AgentStats(
                agent_id,
                str(ev.payload.get("name", agent_id)),
                int(ev.payload.get("qi_max", 0)),
                int(ev.payload.get("starting_stones", 0)),
            )
        elif ev.kind is EventKind.ACTION:
            st = stats[ev.actor]
            st.fold_deltas(ev)
            if ev.qi_delta < 0:
                qi_surcharges += -ev.qi_delta
            atype = str(ev.payload.get("type", ""))
            st.actions[atype] += 1
            if ev.payload.get("degraded"):
                st.degraded += 1
                if ev.payload.get("reason") == "malformed":
                    st.malformed += 1
            elif atype == "converse":
                st.utterances += 1
            elif atype == "note":
                st.notes_written += 1
        elif ev.kind is EventKind.LLM_CALL:
            st = stats[ev.actor]
            st.fold_deltas(ev)
            st.tokens_in += int(ev.payload.get("usage_in", 0))
            st.tokens_out += int(ev.payload.get("usage_out", 0))
            llm_calls += 1
            tokens_in += int(ev.payload.get("usage_in", 0))
            tokens_out += int(ev.payload.get("usage_out", 0))
            if ev.qi_delta < 0:
                qi_thinking += -ev.qi_delta
        elif ev.kind is EventKind.LEDGER_ADJUST:
            stats[ev.actor].fold_deltas(ev)
        elif ev.kind is EventKind.TASK_ATTEMPT:
            claims = ev.payload.get("claims")
            for claim in claims if isinstance(claims, list) else []:
                if not isinstance(claim, dict):
                    continue
                task_id = str(claim.get("task_id", ""))
                tier = int(claim.get("tier", 0))
                discoveries.append(
                    {
                        "day": ev.day,
                        "agent_id": ev.actor,
                        "name": stats[ev.actor].name,
                        "task_id": task_id,
                        "tier": tier,
                        "first_in_world": bool(claim.get("first", False)),
                    }
                )
                if task_id not in verified_tiers:
                    verified_tiers[task_id] = tier
        elif ev.kind is EventKind.AGENT_DIED:
            stats[ev.actor].alive = False
        elif ev.kind is EventKind.RUN_FINISHED:
            recorded = ev.payload.get("days_elapsed")
            if isinstance(recorded, int) and not isinstance(recorded, bool):
                days = recorded

    tier_histogram: Counter[str] = Counter(str(t) for t in verified_tiers.values())
    return {
        "run_id": run_id,
        "mode": mode,
        "model_id": model_id,
        "days": days,
        "agents": [stats[aid].as_dict() for aid in spawn_order],
        "discoveries": discoveries,
        "distinct_verified": len(verified_tiers),
        "tier_histogram": {k: tier_histogram[k] for k in sorted(tier_histogram)},
        "totals": {
            "llm_calls": llm_calls,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "qi_thinking": qi_thinking,
            "qi_surcharges": qi_surcharges,
        },
    }


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _render_md(data: dict[str, Any]) -> str:
    """Readable markdown twin of report.json (same fold, same ordering)."""
    totals = data["totals"]
    lines = [
        f"# lamarck run {data['run_id']}",
        "",
        f"- mode: {data['mode']}",
        f"- model: {data['model_id'] or '(none)'}",
        f"- days: {data['days']}",
        f"- distinct verified discoveries: {data['distinct_verified']}",
        f"- tokens: {totals['tokens_in']} in / {totals['tokens_out']} out "
        f"over {totals['llm_calls']} calls",
        f"- qi from thinking: {totals['qi_thinking']} / "
        f"qi from surcharges: {totals['qi_surcharges']}",
        "",
        "## Discoveries",
        "",
    ]
    if data["discoveries"]:
        rows = [
            [
                str(d["day"]),
                f"{d['name']} ({d['agent_id']})",
                d["task_id"],
                str(d["tier"]),
                "yes" if d["first_in_world"] else "no",
            ]
            for d in data["discoveries"]
        ]
        lines.extend(_md_table(["day", "agent", "task", "tier", "first"], rows))
    else:
        lines.append("(none)")
    lines.extend(["", "## Tier histogram (distinct verified)", ""])
    if data["tier_histogram"]:
        lines.extend(f"- tier {tier}: {count}" for tier, count in data["tier_histogram"].items())
    else:
        lines.append("(none)")
    lines.extend(["", "## Agents", ""])
    rows = [
        [
            f"{a['name']} ({a['agent_id']})",
            "alive" if a["alive"] else "dead",
            str(a["qi_spent"]),
            str(a["qi_remaining"]),
            str(a["stones"]),
            str(a["degraded"]),
            str(a["malformed"]),
            str(a["utterances"]),
            str(a["notes_written"]),
            f"{a['tokens_in']}/{a['tokens_out']}",
        ]
        for a in data["agents"]
    ]
    lines.extend(
        _md_table(
            [
                "agent",
                "state",
                "qi spent",
                "qi left",
                "stones",
                "degraded",
                "malformed",
                "spoke",
                "notes",
                "tokens in/out",
            ],
            rows,
        )
    )
    lines.extend(["", "## Actions", ""])
    for a in data["agents"]:
        histogram = ", ".join(f"{k}: {v}" for k, v in a["actions"].items()) or "(none)"
        lines.append(f"- {a['name']} ({a['agent_id']}): {histogram}")
    lines.append("")
    return "\n".join(lines)


def write_report(run_dir: str | Path) -> Path:
    """Write ``report.json`` and ``report.md`` for *run_dir*; return the
    ``report.json`` path. Pure fold of the event log — deterministic and
    byte-identical across re-writes (see module docstring)."""
    rd = Path(run_dir)
    with EventStore(rd / "events.sqlite3") as store:
        events = list(store.scan())
    data = _fold_report(events)
    json_path = rd / "report.json"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (rd / "report.md").write_text(_render_md(data), encoding="utf-8")
    return json_path
