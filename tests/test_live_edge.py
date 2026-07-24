"""Edge-path tests for the live runner: allowance exhaustion, first-in-world
bounties, the trade retry funnel, stone-poverty degradation, travel
validation, and deep replay's prompt-sha teeth.

Each test builds a tiny valley-derived config via nested ``model_copy`` and
drives ``run_live`` with a purpose-built scripted callable (the general
coverage policy lives in tests/scripted_llm.py; these scripts are precise
by-name/by-round tables instead). Repo personas: a1 "Yan Hua", a2 "Bo
Shan", a3 "Mei Lin".
"""

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from lamarck.contracts import GENESIS_HASH, EventKind
from lamarck.engine import load_live_config
from lamarck.eventstore import EventStore, TextsStore, canonical_bytes, sha256_hex
from lamarck.live import replay_live, run_live
from lamarck.serving import ScriptedBackend
from lamarck.universes import WuxingUniverse

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"

_NAME_RE = re.compile(r"^You are (.+?), a cultivator", re.MULTILINE)
_DAY_RE = re.compile(r"^Day (\d+), round (\d+), tick (\d+)\.")


def _cfg(
    *,
    founders: int,
    days: int,
    rounds: int,
    allowance: int | None = None,
    qi_max: int | None = None,
    starting_stones: int | None = None,
):
    cfg = load_live_config(VALLEY_TOML)
    update = {
        "world": cfg.world.model_copy(update={"days": days, "rounds_per_day": rounds}),
        "model": cfg.model.model_copy(update={"backend": "scripted"}),
        "population": cfg.population.model_copy(update={"founders": founders}),
    }
    qi_update = {}
    if allowance is not None:
        qi_update["daily_allowance"] = allowance
    if qi_max is not None:
        qi_update["qi_max"] = qi_max
    if qi_update:
        update["qi"] = cfg.qi.model_copy(update=qi_update)
    if starting_stones is not None:
        update["economy"] = cfg.economy.model_copy(update={"starting_stones": starting_stones})
    return cfg.model_copy(update=update)


def _ctx(prompt: str) -> tuple[str, int, int, bool, bool]:
    """(persona name, day, round, is_dusk, is_retry) from an envelope."""
    envelope = json.loads(prompt)
    name = _NAME_RE.search(envelope["system"]).group(1)
    user = envelope["user"]
    if user.startswith("Dusk of day"):
        return name, -1, -1, True, False
    match = _DAY_RE.match(user)
    assert match, f"unrecognized user header: {user[:60]!r}"
    return name, int(match.group(1)), int(match.group(2)), False, "Your reply was invalid" in user


def _act(action: str, **args: object) -> str:
    return json.dumps({"action": action, **args})


def _events(run_dir: Path):
    with EventStore(run_dir / "events.sqlite3") as store:
        return list(store.scan())


def _tier1_recipe(cfg) -> tuple[str, list[list[str]]]:
    """(task_id, steps) of the universe's first tier-1 commission, found via
    the TEST-ONLY _debug_rules escape hatch."""
    universe = WuxingUniverse(cfg.universe.seed, cfg.universe.tiers)
    task = universe.tasks(1)[0]
    target = task.title.split('"')[1]
    rules = universe._debug_rules()
    pair_key = next(key for key, product in rules.items() if product == target)
    a, b = pair_key.split("+")
    return task.task_id, [[a, b]]


# ------------------------------------------------------- allowance exhaustion


def test_allowance_exhaustion_skips_thinking_and_reflection(tmp_path):
    """A tiny daily allowance fails the pre-check: no model call is made at
    all (the backend would raise), every tick degrades to a "spent" rest,
    and dusk reflections are skipped without emitting anything."""

    def never_called(prompt, params):
        raise AssertionError("backend must not be consulted when the pre-check fails")

    cfg = _cfg(founders=2, days=1, rounds=2, allowance=100)
    summary = run_live(
        cfg, tmp_path / "run", config_path=None, backend=ScriptedBackend(never_called)
    )
    assert summary.llm_calls == 0
    assert summary.spent_skips == 4  # 2 agents x 2 rounds
    assert summary.degraded == 4
    assert summary.reflections == 0
    assert summary.reflections_skipped == 2
    assert summary.retries == 0

    events = _events(tmp_path / "run")
    assert not [ev for ev in events if ev.kind is EventKind.LLM_CALL]
    assert not [ev for ev in events if ev.kind is EventKind.REFLECTION]
    actions = [ev for ev in events if ev.kind is EventKind.ACTION]
    assert len(actions) == 4
    for ev in actions:
        assert ev.payload == {"type": "rest", "degraded": True, "reason": "spent"}
        assert (ev.qi_delta, ev.stones_delta) == (0, 0)  # valley rest surcharge is 0
    assert replay_live(tmp_path / "run").ok


# ------------------------------------------------------------- first-in-world


def test_first_in_world_multiplier_exactly_once_per_task(tmp_path):
    """Two agents verify the SAME commission: only the chronologically first
    attempt carries first_in_world and the x3 bounty; every later
    verification (same day or later days) pays the base bounty."""
    cfg = _cfg(founders=2, days=2, rounds=1)
    task_id, steps = _tier1_recipe(cfg)

    def script(prompt, params):
        _, _, _, dusk, retry = _ctx(prompt)
        if dusk:
            return "the crucible cools"
        assert not retry, "the scripted experiment must be valid"
        return _act("experiment", task_id=task_id, steps=steps)

    summary = run_live(cfg, tmp_path / "run", config_path=None, backend=ScriptedBackend(script))
    assert summary.attempts == 4  # 2 agents x 2 days x 1 round
    assert summary.discoveries == 4
    assert summary.distinct_discoveries == 1
    assert summary.first_discoveries == 1

    events = _events(tmp_path / "run")
    attempts = [ev for ev in events if ev.kind is EventKind.TASK_ATTEMPT]
    assert [ev.payload["verified"] for ev in attempts] == [True] * 4
    assert [ev.payload["first_in_world"] for ev in attempts] == [True, False, False, False]
    adjusts = [
        ev
        for ev in events
        if ev.kind is EventKind.LEDGER_ADJUST and ev.payload["reason"] == "bounty"
    ]
    base = cfg.economy.bounties[0]
    assert [ev.stones_delta for ev in adjusts] == [base * 3, base, base, base]
    assert [ev.payload["first"] for ev in adjusts] == [True, False, False, False]
    # The first verifier is whoever the scheduler shuffled first; the second
    # agent's day-0 verification already pays base.
    assert adjusts[0].actor != adjusts[1].actor
    assert replay_live(tmp_path / "run").ok


# --------------------------------------------------------------- trade funnel


@pytest.fixture(scope="module")
def trade_funnel_run(tmp_path_factory):
    """One run exercising every trade rejection (poor/dead/self/elsewhere),
    a successful trade, and travel-to-current — all via the retry path.

    Mei Lin (a3) shouts 6000-char utterances: her calls bill ~1900 qi each,
    so with qi_max=4000 she is dead by dusk of day 0 (the others' short
    replies keep them alive through day 0). Day 1 trades then target a dead
    agent, the self, and a traveler."""
    blast = "x" * 6000

    def script(prompt, params):
        name, day, rnd, dusk, retry = _ctx(prompt)
        if dusk:
            return blast if name == "Mei Lin" else "a quiet dusk"
        if retry:
            return _act("meditate")
        if name == "Mei Lin":
            return _act("converse", target="Mei Lin", text=blast)
        if name == "Bo Shan":
            if day == 0 and rnd == 0:
                return _act("travel", to="meadow")  # current -> semantic retry
            if day == 1 and rnd == 0:
                return _act("travel", to="furnace-hall")
            return _act("meditate")
        # Yan Hua
        if day == 0 and rnd == 0:
            return _act("trade", target="Bo Shan", stones=999)  # poorer than that
        if day == 0 and rnd == 1:
            return _act("trade", target="Bo Shan", stones=3)  # succeeds
        if day == 1 and rnd == 0:
            return _act("trade", target="Mei Lin", stones=1)  # dead
        if day == 1 and rnd == 1:
            return _act("trade", target="Yan Hua", stones=1)  # self
        if day == 1 and rnd == 2:
            return _act("trade", target="Bo Shan", stones=1)  # traveled away
        return _act("meditate")

    cfg = _cfg(founders=3, days=2, rounds=3, qi_max=4000)
    run_dir = tmp_path_factory.mktemp("trade-funnel") / "run"
    summary = run_live(cfg, run_dir, config_path=None, backend=ScriptedBackend(script))
    return cfg, run_dir, summary, _events(run_dir)


def _retry_reasons(run_dir: Path, events) -> list[str]:
    """The 'Your reply was invalid: ...' first lines of every retry prompt,
    read back from the llm_texts side table."""
    slots: dict[tuple[int, int, str], list] = {}
    for ev in events:
        if ev.kind is EventKind.LLM_CALL and ev.payload["purpose"] == "tick":
            slots.setdefault((ev.day, ev.tick, ev.actor), []).append(ev)
    reasons = []
    with TextsStore(run_dir / "events.sqlite3") as texts:
        for calls in slots.values():
            if len(calls) == 2:
                user = json.loads(texts.get(calls[1].seq))["user"]
                marker = user.rindex("Your reply was invalid: ")
                reasons.append(user[marker:].split("\n")[0])
    return reasons


def test_trade_funnel_rejections_take_the_retry_path(trade_funnel_run):
    _, run_dir, summary, events = trade_funnel_run
    assert summary.retries == 5
    assert summary.retry_recovered == 5
    assert summary.malformed_forfeits == 0
    assert summary.degraded == 0

    reasons = _retry_reasons(run_dir, events)
    assert len(reasons) == 5
    joined = "\n".join(reasons)
    assert "you have only 20 spirit stones" in joined
    assert "Mei Lin is dead" in joined
    assert "you cannot trade with yourself" in joined
    assert "Bo Shan is not here" in joined
    assert "you are already at meadow" in joined


def test_trade_funnel_death_and_successful_trade(trade_funnel_run):
    cfg, _, _, events = trade_funnel_run
    day0_deaths = [ev.actor for ev in events if ev.kind is EventKind.AGENT_DIED and ev.day == 0]
    assert day0_deaths == ["a3"]  # Mei Lin's own verbosity kills her

    trades = [
        ev
        for ev in events
        if ev.kind is EventKind.ACTION
        and ev.payload.get("type") == "trade"
        and not ev.payload.get("degraded")
    ]
    assert len(trades) == 1
    trade = trades[0]
    assert trade.actor == "a1"
    assert trade.payload == {"type": "trade", "target": "Bo Shan", "stones": 3}
    assert trade.stones_delta == -3
    assert trade.qi_delta == -cfg.qi.action_costs["trade"]
    adjusts = [
        ev
        for ev in events
        if ev.kind is EventKind.LEDGER_ADJUST and ev.payload.get("reason") == "trade"
    ]
    assert len(adjusts) == 1
    assert adjusts[0].seq == trade.seq + 1
    assert adjusts[0].actor == "a2"
    assert adjusts[0].payload == {"reason": "trade", "from": "a1"}
    assert adjusts[0].stones_delta == 3


def test_trade_funnel_replays(trade_funnel_run):
    _, run_dir, _, _ = trade_funnel_run
    shallow = replay_live(run_dir)
    assert shallow.ok, shallow.mismatches
    deep = replay_live(run_dir, deep=True)
    assert deep.ok, deep.mismatches


# ------------------------------------------------------ stone-poverty degrade


def test_experiment_stone_poverty_degrades_with_wanted_payload(tmp_path):
    cfg = _cfg(founders=1, days=1, rounds=1, starting_stones=0)
    task_id, steps = _tier1_recipe(cfg)

    def script(prompt, params):
        _, _, _, dusk, retry = _ctx(prompt)
        if dusk:
            return "no stones, no alchemy"
        assert not retry, "a valid-but-unaffordable action must not retry"
        return _act("experiment", task_id=task_id, steps=steps)

    summary = run_live(cfg, tmp_path / "run", config_path=None, backend=ScriptedBackend(script))
    assert summary.attempts == 0
    assert summary.degraded == 1
    assert summary.retries == 0

    events = _events(tmp_path / "run")
    assert not [ev for ev in events if ev.kind is EventKind.TASK_ATTEMPT]
    actions = [ev for ev in events if ev.kind is EventKind.ACTION]
    assert len(actions) == 1
    assert actions[0].payload == {
        "type": "rest",
        "degraded": True,
        "wanted": {"type": "experiment", "task_id": task_id, "steps": steps},
    }
    assert (actions[0].qi_delta, actions[0].stones_delta) == (0, 0)  # rest surcharge is 0


# --------------------------------------------------- deep replay's sha teeth


def _rechain(db_path: Path) -> None:
    """Recompute the whole hash chain over (possibly tampered) rows — a
    'consistent' history rewrite only deep replay can catch."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seq, day, tick, kind, actor, payload, qi_delta, stones_delta "
            "FROM events ORDER BY seq"
        ).fetchall()
        prev = GENESIS_HASH
        for seq, day, tick, kind, actor, payload_text, qi_delta, stones_delta in rows:
            envelope = {
                "seq": seq,
                "day": day,
                "tick": tick,
                "kind": kind,
                "actor": actor,
                "payload": json.loads(payload_text),
                "qi_delta": qi_delta,
                "stones_delta": stones_delta,
            }
            new_hash = sha256_hex(prev.encode("utf-8") + canonical_bytes(envelope))
            conn.execute("UPDATE events SET hash = ? WHERE seq = ?", (new_hash, seq))
            prev = new_hash
        conn.commit()
    finally:
        conn.close()


def test_deep_replay_reports_prompt_sha_mismatch(tmp_path):
    """Tamper one LLM_CALL's prompt_sha256 and rewrite the chain so shallow
    replay still passes: only deep replay (re-deriving prompts from state)
    catches it — and it REPORTS the mismatch instead of asserting through."""

    def script(prompt, params):
        _, _, _, dusk, _ = _ctx(prompt)
        return "an uneventful dusk" if dusk else _act("meditate")

    cfg = _cfg(founders=2, days=1, rounds=1)
    run_dir = tmp_path / "run"
    run_live(cfg, run_dir, config_path=None, backend=ScriptedBackend(script))

    tampered = tmp_path / "tampered"
    shutil.copytree(run_dir, tampered)
    db_path = tampered / "events.sqlite3"
    conn = sqlite3.connect(db_path)
    try:
        seq, payload_text = conn.execute(
            "SELECT seq, payload FROM events WHERE kind = 'llm_call' ORDER BY seq LIMIT 1"
        ).fetchone()
        payload = json.loads(payload_text)
        payload["prompt_sha256"] = "0" * 64
        conn.execute(
            "UPDATE events SET payload = ? WHERE seq = ?",
            (canonical_bytes(payload).decode("utf-8"), seq),
        )
        conn.commit()
    finally:
        conn.close()
    _rechain(db_path)

    shallow = replay_live(tampered)
    assert shallow.ok, shallow.mismatches  # the rewrite is chain-consistent

    deep = replay_live(tampered, deep=True)
    assert deep.ok is False
    joined = "\n".join(deep.mismatches)
    assert "prompt_sha mismatch" in joined
    assert deep.mode == "deep"
