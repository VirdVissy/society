"""Tests for lamarck.analysis.retread — pair-level retread and call-truncation
statistics.

Every rule in the module docstring is pinned here on hand-built synthetic
event lists (no real hashes needed: the fold never reads ``hash``) with
hand-computed expected numbers; a scripted live run proves the write is a
pure, byte-identical function of the log; and a skip-if-missing test runs
on the accepted Phase-1 log with loose structural bounds only (the exact
baseline numbers are pinned by the coordinator after review).
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from lamarck.analysis.retread import (
    KNOWN_SLAG_TOP_N,
    fold_retread,
    fold_truncation,
    write_retread,
)
from lamarck.contracts import EventKind, EventRecord, qi_llm_cost
from lamarck.engine import load_live_config
from lamarck.eventstore import EventStore
from lamarck.live import run_live
from lamarck.serving import ScriptedBackend
from tests.scripted_llm import make as make_scripted

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"
ACCEPT2_LOG = REPO_ROOT / "runs" / "phase1-accept2" / "events.sqlite3"

# ------------------------------------------------------------- builders


class _Log:
    """Appends EventRecords with contiguous seqs and a dummy hash."""

    def __init__(self) -> None:
        self.events: list[EventRecord] = []

    def add(
        self,
        kind: EventKind,
        *,
        day: int = 0,
        tick: int = 0,
        actor: str = "",
        payload: dict[str, Any] | None = None,
        qi_delta: int = 0,
    ) -> EventRecord:
        ev = EventRecord(
            seq=len(self.events),
            hash="0" * 64,
            day=day,
            tick=tick,
            kind=kind,
            actor=actor,
            payload=payload or {},
            qi_delta=qi_delta,
            stones_delta=0,
        )
        self.events.append(ev)
        return ev

    def spawn(self, agent_id: str, name: str, *, day: int = 0, tick: int = 0) -> None:
        self.add(
            EventKind.AGENT_SPAWNED,
            day=day,
            tick=tick,
            payload={"agent_id": agent_id, "qi_max": 1000, "starting_stones": 5, "name": name},
        )

    def day_started(self, day: int) -> None:
        self.add(EventKind.DAY_STARTED, day=day, payload={"day": day})

    def died(self, agent_id: str, day: int, tick: int = 9) -> None:
        self.add(EventKind.AGENT_DIED, day=day, tick=tick, actor=agent_id, payload={"cause": "qi"})

    def attempt(
        self,
        actor: str,
        day: int,
        tick: int,
        steps: list[list[str]],
        products: list[str],
    ) -> None:
        final = products[-1] if products else ""
        self.add(
            EventKind.TASK_ATTEMPT,
            day=day,
            tick=tick,
            actor=actor,
            payload={
                "steps": steps,
                "step_products": products,
                "message": f"the crucible yields {final}",
                "claims": [],
            },
        )

    def call(
        self,
        actor: str,
        day: int,
        tick: int,
        purpose: str,
        usage_in: int,
        usage_out: int,
        max_tokens: int,
    ) -> None:
        self.add(
            EventKind.LLM_CALL,
            day=day,
            tick=tick,
            actor=actor,
            payload={
                "purpose": purpose,
                "agent": actor,
                "model_id": "m",
                "adapter": "",
                "prompt_sha256": "f" * 64,
                "temp_permille": 700,
                "max_tokens": max_tokens,
                "seed": len(self.events),
                "response": "x",
                "usage_in": usage_in,
                "usage_out": usage_out,
            },
            qi_delta=-qi_llm_cost(usage_in, usage_out),
        )

    def action(self, actor: str, day: int, tick: int, cost: int, **payload: Any) -> None:
        self.add(EventKind.ACTION, day=day, tick=tick, actor=actor, payload=payload, qi_delta=-cost)


def _tally(steps: int, novel: int, slag: int, recipe: int, permille: int) -> dict[str, int]:
    return {
        "steps": steps,
        "novel": novel,
        "retread_slag": slag,
        "retread_recipe": recipe,
        "retread": slag + recipe,
        "retread_permille": permille,
    }


def _assert_ints_only(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str)
            _assert_ints_only(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _assert_ints_only(item, f"{path}[{i}]")
    else:
        assert isinstance(value, str | int | bool), f"non-int/str/bool at {path}: {value!r}"
        assert not isinstance(value, float)


# ------------------------------------------------------- retread: synthetic


def _retread_log() -> list[EventRecord]:
    """Founder a1 (spawn day 0, dies day 3) and successor a2 (spawn day 2,
    tick 3, alive); DAY_STARTED for days 0..4. Every number below is
    hand-derived in the tests that consume this log."""
    log = _Log()
    log.spawn("a1", "Yan Hua")
    log.day_started(0)
    # attempt 1: the same pair twice inside one attempt -> second is retread_slag
    log.attempt("a1", 0, 1, [["fire", "wood"], ["wood", "fire"]], ["slag", "slag"])
    log.day_started(1)
    # attempt 2: a novel recipe
    log.attempt("a1", 1, 1, [["earth", "water"]], ["cinnabar-ash"])
    # attempt 3: retread_recipe (reversed order), novel, retread_slag
    log.attempt(
        "a1",
        1,
        2,
        [["water", "earth"], ["cinnabar-ash", "fire"], ["fire", "wood"]],
        ["cinnabar-ash", "azure-dew", "slag"],
    )
    log.day_started(2)
    log.spawn("a2", "Bo Shan", day=2, tick=3)  # a successor, mid-run and mid-day
    log.attempt("a2", 2, 4, [["earth", "fire"]], ["slag"])
    log.day_started(3)
    # attempt 4 (a1): only the first step executed; the second is not counted
    log.attempt("a1", 3, 1, [["fire", "wood"], ["metal", "metal"]], ["slag"])
    log.attempt("a2", 3, 2, [["fire", "earth"], ["earth", "fire"]], ["slag", "slag"])
    log.attempt("a2", 3, 3, [["jade-root", "fire"]], [])  # halted: nothing executed
    log.died("a1", 3)
    log.day_started(4)
    return log.events


def test_retread_per_agent_counts() -> None:
    out = fold_retread(_retread_log())
    a1, a2 = out["agents"]
    assert [a["agent_id"] for a in (a1, a2)] == ["a1", "a2"]
    assert (a1["name"], a1["spawn_day"]) == ("Yan Hua", 0)
    assert (a2["name"], a2["spawn_day"]) == ("Bo Shan", 2)
    # a1: 7 executed steps = novel 3, retread_slag 3, retread_recipe 1 -> 4/7 = 571.4
    for key, value in _tally(7, 3, 3, 1, 571).items():
        assert a1[key] == value, key
    assert (a1["attempts"], a1["attempts_empty"], a1["attempts_zero_novel"]) == (4, 0, 1)
    assert a1["max_repeats_of_one_pair"] == {"count": 4, "pair": ["fire", "wood"]}
    # a2: 3 executed steps = novel 1, retread_slag 2 -> 2/3 = 666.67 floors to 666
    for key, value in _tally(3, 1, 2, 0, 666).items():
        assert a2[key] == value, key
    assert (a2["attempts"], a2["attempts_empty"], a2["attempts_zero_novel"]) == (3, 1, 1)
    assert a2["max_repeats_of_one_pair"] == {"count": 3, "pair": ["earth", "fire"]}


def test_retread_population_and_known_slag_top() -> None:
    out = fold_retread(_retread_log())
    population = out["population"]
    assert population["agents"] == 2
    for key, value in _tally(10, 4, 5, 1, 600).items():
        assert population[key] == value, key
    assert population["attempts"] == 7
    assert population["attempts_empty"] == 1
    assert population["attempts_zero_novel"] == 2
    assert out["known_slag_retries_top"] == [
        {"agent_id": "a1", "pair": ["fire", "wood"], "count": 3},
        {"agent_id": "a2", "pair": ["earth", "fire"], "count": 2},
    ]


def test_retread_life_day_series_is_dense_and_age_indexed() -> None:
    out = fold_retread(_retread_log())
    a1, a2 = out["agents"]
    # a1 lived days 0..3 (died at day 3; no row for day 4)
    assert a1["by_life_day"] == [
        {"life_day": 0, **_tally(2, 1, 1, 0, 500)},
        {"life_day": 1, **_tally(4, 2, 1, 1, 500)},
        {"life_day": 2, **_tally(0, 0, 0, 0, 0)},
        {"life_day": 3, **_tally(1, 0, 1, 0, 1000)},
    ]
    # a2 spawned on day 2: life-day 0 is calendar day 2, and day 4 (alive) is a zero row
    assert a2["by_life_day"] == [
        {"life_day": 0, **_tally(1, 1, 0, 0, 0)},
        {"life_day": 1, **_tally(2, 0, 2, 0, 1000)},
        {"life_day": 2, **_tally(0, 0, 0, 0, 0)},
    ]


def test_retread_by_day_is_dense_over_calendar_days() -> None:
    out = fold_retread(_retread_log())
    assert out["retread_by_day"] == [
        {"day": 0, **_tally(2, 1, 1, 0, 500)},
        {"day": 1, **_tally(4, 2, 1, 1, 500)},
        {"day": 2, **_tally(1, 1, 0, 0, 0)},
        {"day": 3, **_tally(3, 0, 3, 0, 1000)},
        {"day": 4, **_tally(0, 0, 0, 0, 0)},
    ]


def test_retread_repeat_inside_one_attempt_uses_the_updated_journal() -> None:
    log = _Log()
    log.spawn("a1", "Yan Hua")
    log.day_started(0)
    log.attempt("a1", 0, 1, [["fire", "wood"], ["wood", "fire"]], ["slag", "slag"])
    log.attempt("a1", 0, 2, [["earth", "water"], ["water", "earth"]], ["c-ash", "c-ash"])
    a1 = fold_retread(log.events)["agents"][0]
    assert (a1["novel"], a1["retread_slag"], a1["retread_recipe"]) == (2, 1, 1)
    assert a1["attempts_zero_novel"] == 0


def test_retread_empty_log_and_agent_without_steps() -> None:
    assert fold_retread([]) == {
        "agents": [],
        "population": {
            "agents": 0,
            **_tally(0, 0, 0, 0, 0),
            "attempts": 0,
            "attempts_empty": 0,
            "attempts_zero_novel": 0,
        },
        "retread_by_day": [],
        "known_slag_retries_top": [],
    }
    log = _Log()
    log.spawn("a1", "Yan Hua")
    a1 = fold_retread(log.events)["agents"][0]
    assert a1["max_repeats_of_one_pair"] == {"count": 0, "pair": []}
    assert a1["by_life_day"] == [{"life_day": 0, **_tally(0, 0, 0, 0, 0)}]


def test_known_slag_top_is_capped_and_ordered() -> None:
    log = _Log()
    log.spawn("a1", "Yan Hua")
    log.day_started(0)
    bases = ["wood", "fire", "earth", "metal", "water", "b6", "b7", "b8", "b9", "b10", "b11", "b12"]
    for i, base in enumerate(bases):
        # pair (base, "zz") tried 2 + (i % 3) times: 1 novel + (1 + i % 3) slag retreads
        steps = [[base, "zz"]] * (2 + i % 3)
        log.attempt("a1", 0, i + 1, steps, ["slag"] * len(steps))
    top = fold_retread(log.events)["known_slag_retries_top"]
    assert len(top) == KNOWN_SLAG_TOP_N == 10
    counts = [row["count"] for row in top]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 3
    # ties at the same count are ordered by pair
    threes = [row["pair"] for row in top if row["count"] == 3]
    assert threes == sorted(threes)


# ---------------------------------------------------- truncation: synthetic


def _truncation_log() -> list[EventRecord]:
    """Eight tick calls in six slots (three retries, two after truncation),
    two reflections (one truncated), a2 dies at dusk of day 0."""
    log = _Log()
    log.spawn("a1", "Yan Hua")
    log.spawn("a2", "Bo Shan")
    log.day_started(0)
    # slot a1/d0/t1: truncated primary (470 qi) + retry (315 qi) -> experiment
    log.call("a1", 0, 1, "tick", 1000, 220, 220)
    log.call("a1", 0, 1, "tick", 1100, 40, 220)
    log.action("a1", 0, 1, 100, type="experiment", steps=[["fire", "wood"]])
    # slot a2/d0/t2: single clean call (300 qi) -> note
    log.call("a2", 0, 2, "tick", 800, 100, 220)
    log.action("a2", 0, 2, 30, type="note", text="hm")
    # slot a1/d0/t3: truncated but parsed, NO retry (470 qi) -> converse
    log.call("a1", 0, 3, "tick", 1000, 220, 220)
    log.action("a1", 0, 3, 50, type="converse", target="Bo Shan", text="hi")
    # slot a2/d0/t4: clean primary (285) + clean retry (308) -> malformed forfeit
    log.call("a2", 0, 4, "tick", 900, 60, 220)
    log.call("a2", 0, 4, "tick", 950, 70, 220)
    log.action("a2", 0, 4, 10, type="rest", degraded=True, reason="malformed")
    # slot a1/d0/t5: spent pre-check, no call at all
    log.action("a1", 0, 5, 0, type="rest", degraded=True, reason="spent", free=True)
    # dusk tick 6: a1 reflection truncated (285), a2 reflection clean (190)
    log.call("a1", 0, 6, "reflection", 500, 160, 160)
    log.add(EventKind.REFLECTION, day=0, tick=6, actor="a1", payload={"text": "..."})
    log.call("a2", 0, 6, "reflection", 400, 90, 160)
    log.add(EventKind.REFLECTION, day=0, tick=6, actor="a2", payload={"text": "..."})
    log.died("a2", 0, 6)
    log.day_started(1)
    # slot a1/d1/t1: truncated primary (520) + retry (355) -> experiment
    log.call("a1", 1, 1, "tick", 1200, 220, 220)
    log.call("a1", 1, 1, "tick", 1300, 30, 220)
    log.action("a1", 1, 1, 100, type="experiment", steps=[["earth", "water"]])
    return log.events


def test_truncation_by_purpose() -> None:
    out = fold_truncation(_truncation_log())
    assert sorted(out["by_purpose"]) == ["reflection", "tick"]
    tick = out["by_purpose"]["tick"]
    assert (tick["calls"], tick["truncated"], tick["truncated_permille"]) == (8, 3, 375)
    # non-truncated usage_out sample: [30, 40, 60, 70, 100] -> nearest rank p50 = 3rd, p90 = 5th
    assert (tick["usage_out_p50"], tick["usage_out_p90"], tick["usage_out_max"]) == (60, 100, 100)
    assert tick["max_tokens_seen"] == [220]
    reflection = out["by_purpose"]["reflection"]
    assert (reflection["calls"], reflection["truncated"], reflection["truncated_permille"]) == (
        2,
        1,
        500,
    )
    assert (reflection["usage_out_p50"], reflection["usage_out_p90"]) == (90, 90)
    assert reflection["max_tokens_seen"] == [160]


def test_truncation_slots_and_qi() -> None:
    out = fold_truncation(_truncation_log())
    assert out["slots"] == {
        "actions": 6,
        "actions_no_call": 1,
        "actions_single_call": 2,
        "actions_with_retry": 3,
        "retry_permille": 500,
        "actions_with_retry_after_truncation": 2,
        "malformed_forfeits": 1,
    }
    # qi per call: 470, 315, 300, 470, 285, 308, 520, 355 (tick) + 285, 190 (reflection)
    assert out["qi"] == {
        "qi_thinking_total": 3498,
        "truncated_then_retried_calls": 2,
        "qi_on_truncated_first_calls": 470 + 520,
        "share_permille": 283,  # 990 / 3498 = 283.02
        "qi_on_retry_calls": 315 + 308 + 355,
    }


def test_truncation_per_agent_prompt_length_vs_qi_per_day() -> None:
    out = fold_truncation(_truncation_log())
    a1, a2 = out["agents"]
    assert a1 == {
        "agent_id": "a1",
        "name": "Yan Hua",
        "alive": True,
        "spawn_day": 0,
        "death_day": -1,
        "calls": 6,
        "usage_in": 6100,
        "mean_usage_in": 1016,  # 6100 / 6 = 1016.67
        "tick_calls": 5,
        "usage_in_tick": 5600,
        "mean_usage_in_tick": 1120,
        "qi_thinking": 2415,
        "qi_spent": 2415 + 100 + 50 + 100,
        "days_active": 2,
        "qi_per_day": 1332,  # 2665 / 2 = 1332.5 floors to 1332
    }
    assert a2 == {
        "agent_id": "a2",
        "name": "Bo Shan",
        "alive": False,
        "spawn_day": 0,
        "death_day": 0,
        "calls": 4,
        "usage_in": 3050,
        "mean_usage_in": 762,  # 3050 / 4 = 762.5
        "tick_calls": 3,
        "usage_in_tick": 2650,
        "mean_usage_in_tick": 883,  # 2650 / 3 = 883.33
        "qi_thinking": 1083,
        "qi_spent": 1083 + 30 + 10,
        "days_active": 1,
        "qi_per_day": 1123,
    }


def test_truncation_empty_log_keeps_the_shape() -> None:
    out = fold_truncation([])
    assert sorted(out["by_purpose"]) == ["reflection", "tick"]
    assert out["by_purpose"]["tick"] == {
        "calls": 0,
        "truncated": 0,
        "truncated_permille": 0,
        "usage_out_p50": 0,
        "usage_out_p90": 0,
        "usage_out_max": 0,
        "max_tokens_seen": [],
    }
    assert out["slots"]["actions"] == 0 and out["slots"]["retry_permille"] == 0
    assert out["qi"]["share_permille"] == 0
    assert out["agents"] == []


# ------------------------------------------------------ scripted live run


@pytest.fixture(scope="module")
def scripted_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Any, list[EventRecord]]:
    """A small scripted live run (same shape as tests/test_report.py)."""
    cfg = load_live_config(VALLEY_TOML)
    cfg = cfg.model_copy(
        update={
            "world": cfg.world.model_copy(update={"days": 2, "rounds_per_day": 4}),
            "model": cfg.model.model_copy(update={"backend": "scripted"}),
            "population": cfg.population.model_copy(update={"founders": 3}),
        }
    )
    run_dir = tmp_path_factory.mktemp("retread") / "run"
    summary = run_live(
        cfg,
        run_dir,
        config_path=None,
        backend=ScriptedBackend(make_scripted()),
        difftest_interval=0,
    )
    with EventStore(run_dir / "events.sqlite3") as store:
        events = list(store.scan())
    return run_dir, summary, events


def test_scripted_two_writes_are_byte_identical(
    scripted_run: tuple[Path, Any, list[EventRecord]], tmp_path: Path
) -> None:
    run_dir, summary, _ = scripted_run
    first = write_retread(run_dir, out_path=tmp_path / "one.json")
    second = write_retread(run_dir, out_path=tmp_path / "two.json")
    assert first == tmp_path / "one.json" and second == tmp_path / "two.json"
    assert first.read_bytes() == second.read_bytes()
    data = json.loads(first.read_text(encoding="utf-8"))
    assert sorted(data) == ["retread", "run_id", "truncation"]
    assert data["run_id"] == summary.run_id
    assert sorted(data["retread"]) == [
        "agents",
        "known_slag_retries_top",
        "population",
        "retread_by_day",
    ]
    assert sorted(data["truncation"]) == ["agents", "by_purpose", "qi", "slots"]
    _assert_ints_only(data)
    assert not (run_dir / "retread.json").exists()  # out_path redirected the write


def test_scripted_default_out_path_is_run_dir(
    scripted_run: tuple[Path, Any, list[EventRecord]],
) -> None:
    run_dir, _, _ = scripted_run
    path = write_retread(run_dir)
    assert path == run_dir / "retread.json"
    assert path.read_bytes() == write_retread(run_dir).read_bytes()


def test_scripted_totals_re_derive_from_the_log(
    scripted_run: tuple[Path, Any, list[EventRecord]],
) -> None:
    _, summary, events = scripted_run
    data = {"retread": fold_retread(events), "truncation": fold_truncation(events)}
    attempts = [ev for ev in events if ev.kind is EventKind.TASK_ATTEMPT]
    population = data["retread"]["population"]
    assert population["attempts"] == len(attempts) == summary.attempts
    assert population["steps"] == sum(len(ev.payload["step_products"]) for ev in attempts)
    assert population["steps"] > 0 and population["retread_recipe"] > 0  # claims re-submit
    assert [a["agent_id"] for a in data["retread"]["agents"]] == ["a1", "a2", "a3"]
    llm = [ev for ev in events if ev.kind is EventKind.LLM_CALL]
    by_purpose = data["truncation"]["by_purpose"]
    assert by_purpose["tick"]["calls"] == sum(1 for ev in llm if ev.payload["purpose"] == "tick")
    assert by_purpose["reflection"]["calls"] == summary.reflections
    assert by_purpose["tick"]["truncated"] == 0  # scripted replies are far below max_tokens
    slots = data["truncation"]["slots"]
    assert slots["actions"] == sum(1 for ev in events if ev.kind is EventKind.ACTION)
    assert slots["actions_with_retry"] == summary.retries
    assert slots["malformed_forfeits"] == summary.malformed_forfeits
    assert data["truncation"]["qi"]["qi_thinking_total"] == sum(-ev.qi_delta for ev in llm)
    assert data["truncation"]["qi"]["qi_on_truncated_first_calls"] == 0


# --------------------------------------------------------- the real log


@pytest.mark.skipif(not ACCEPT2_LOG.exists(), reason="accepted Phase-1 run not present")
def test_phase1_accept2_structural_bounds(tmp_path: Path) -> None:
    """Loose bounds only — exact numbers are pinned after review. The log is
    copied first so the archived run directory is never opened in place."""
    run_dir = tmp_path / "accept2"
    run_dir.mkdir()
    shutil.copyfile(ACCEPT2_LOG, run_dir / "events.sqlite3")
    data = json.loads(write_retread(run_dir).read_text(encoding="utf-8"))
    _assert_ints_only(data)
    retread, truncation = data["retread"], data["truncation"]
    population = retread["population"]
    assert population["agents"] == len(retread["agents"]) == 8
    assert population["steps"] > 0 and population["attempts"] > 0
    assert 500 <= population["retread_permille"] <= 700
    assert population["retread_slag"] > population["retread_recipe"]
    top = retread["known_slag_retries_top"]
    assert len(top) == KNOWN_SLAG_TOP_N
    assert [row["count"] for row in top] == sorted((row["count"] for row in top), reverse=True)
    assert len(retread["retread_by_day"]) == 30
    for agent in retread["agents"]:
        assert 0 <= agent["retread_permille"] <= 1000
        assert len(agent["by_life_day"]) == 30
    tick = truncation["by_purpose"]["tick"]
    assert tick["truncated_permille"] > 300
    assert 0 <= tick["truncated_permille"] <= 1000
    slots = truncation["slots"]
    assert slots["actions"] > 0 and 0 < slots["actions_with_retry"] <= slots["actions"]
    assert slots["actions_with_retry_after_truncation"] <= slots["actions_with_retry"]
    qi = truncation["qi"]
    assert 0 < qi["qi_on_truncated_first_calls"] < qi["qi_thinking_total"]
    assert 0 <= qi["share_permille"] <= 1000
    for agent in truncation["agents"]:
        assert agent["mean_usage_in"] > 0 and agent["qi_per_day"] > 0


# ------------------------------------------------- adversarial (reviewer)


def test_retread_journals_are_per_actor_and_only_task_attempts_count() -> None:
    """Journals never leak between agents; degraded/malformed ACTIONs and a
    TASK_ATTEMPT with non-list ``steps`` contribute no executed step; an
    attempt on a day with no DAY_STARTED still creates its day/life-day rows;
    a tie in max_repeats_of_one_pair goes to the lexicographically smallest
    pair."""
    log = _Log()
    log.spawn("a1", "Yan Hua")
    log.spawn("a2", "Bo Shan")
    log.spawn("a3", "Mei Lin")
    log.day_started(0)
    log.attempt("a1", 0, 1, [["fire", "wood"]], ["slag"])  # a1 novel
    log.attempt("a2", 0, 2, [["wood", "fire"]], ["slag"])  # a2 novel too: journals are per actor
    log.attempt("a1", 0, 3, [["earth", "water"]], ["cinnabar-ash"])  # a1 novel recipe
    # a2: novel recipe, then the same pair again inside the attempt -> retread_recipe
    log.attempt(
        "a2", 0, 4, [["water", "earth"], ["earth", "water"]], ["cinnabar-ash", "cinnabar-ash"]
    )
    # degraded experiment (unaffordable) and a malformed forfeit: no TASK_ATTEMPT, no effect
    log.action(
        "a1",
        0,
        5,
        10,
        type="rest",
        degraded=True,
        wanted={"type": "experiment", "steps": [["fire", "wood"]]},
    )
    log.action("a2", 0, 6, 10, type="rest", degraded=True, reason="malformed")
    # corrupt-shaped attempt: non-list steps => zero executed steps (attempts_empty)
    log.add(
        EventKind.TASK_ATTEMPT,
        day=0,
        tick=8,
        actor="a1",
        payload={"steps": "fire,wood", "step_products": ["slag"], "message": "", "claims": []},
    )
    # a3: (metal, water) x2 and (earth, fire) x2 -> max_repeats tie -> (earth, fire)
    log.attempt(
        "a3",
        0,
        9,
        [["water", "metal"], ["metal", "water"], ["fire", "earth"], ["earth", "fire"]],
        ["slag", "slag", "ember-dust", "ember-dust"],
    )
    # day 1 has NO DAY_STARTED: a1's attempt must still create the rows it lands in
    log.attempt("a1", 1, 1, [["fire", "wood"], ["fire", "wood"]], ["slag", "slag"])
    out = fold_retread(log.events)
    a1, a2, a3 = out["agents"]
    for key, value in _tally(4, 2, 2, 0, 500).items():
        assert a1[key] == value, key
    assert (a1["attempts"], a1["attempts_empty"], a1["attempts_zero_novel"]) == (4, 1, 1)
    assert a1["max_repeats_of_one_pair"] == {"count": 3, "pair": ["fire", "wood"]}
    for key, value in _tally(3, 2, 0, 1, 333).items():
        assert a2[key] == value, key
    assert (a2["attempts"], a2["attempts_empty"], a2["attempts_zero_novel"]) == (2, 0, 0)
    assert a2["max_repeats_of_one_pair"] == {"count": 2, "pair": ["earth", "water"]}
    for key, value in _tally(4, 2, 1, 1, 500).items():
        assert a3[key] == value, key
    assert a3["max_repeats_of_one_pair"] == {"count": 2, "pair": ["earth", "fire"]}
    population = out["population"]
    for key, value in _tally(11, 6, 3, 2, 454).items():  # 5/11 = 454.5.. floors to 454
        assert population[key] == value, key
    assert (population["attempts"], population["attempts_empty"]) == (7, 1)
    assert population["attempts_zero_novel"] == 1
    assert out["known_slag_retries_top"] == [
        {"agent_id": "a1", "pair": ["fire", "wood"], "count": 2},
        {"agent_id": "a3", "pair": ["metal", "water"], "count": 1},
    ]
    assert out["retread_by_day"] == [
        {"day": 0, **_tally(9, 6, 1, 2, 333)},
        {"day": 1, **_tally(2, 0, 2, 0, 1000)},
    ]
    assert a1["by_life_day"] == [
        {"life_day": 0, **_tally(2, 2, 0, 0, 0)},
        {"life_day": 1, **_tally(2, 0, 2, 0, 1000)},
    ]
    # without DAY_STARTED 1 and without an attempt that day, a2/a3 get no life-day 1 row
    assert a2["by_life_day"] == [{"life_day": 0, **_tally(3, 2, 0, 1, 333)}]
    assert a3["by_life_day"] == [{"life_day": 0, **_tally(4, 2, 1, 1, 500)}]


def test_truncation_boundaries_truncated_retry_and_reflection_never_joins_a_slot() -> None:
    """usage_out 219/220/221 against max_tokens 220 is clean/truncated/
    truncated; a clean primary followed by a TRUNCATED retry counts the retry
    as truncated but never as truncated-then-retried; a reflection call at a
    slot's (actor, day, tick) does not enlarge the slot's group; non-int
    usage fields read as 0 and a non-negative qi_delta costs 0; a day whose
    only activity is a spent ACTION still counts as active."""
    log = _Log()
    log.spawn("a1", "Yan Hua")
    log.spawn("a2", "Bo Shan")
    log.day_started(0)
    # slot a1/d0/t1: clean primary at the boundary (219 < 220) + truncated retry -> forfeit
    log.call("a1", 0, 1, "tick", 400, 219, 220)  # 100 + 219 = 319 qi
    log.call("a1", 0, 1, "tick", 500, 220, 220)  # 125 + 220 = 345 qi, truncated, last in group
    log.action("a1", 0, 1, 10, type="rest", degraded=True, reason="malformed")
    # slot a2/d0/t2: usage_out strictly above max_tokens, parsed anyway -> single call
    log.call("a2", 0, 2, "tick", 800, 221, 220)  # 200 + 221 = 421 qi
    log.action("a2", 0, 2, 50, type="converse", target="Yan Hua", text="hi")
    # slot a1/d0/t3: spent pre-check, no call
    log.action("a1", 0, 3, 0, type="rest", degraded=True, reason="spent", free=True)
    # slot a2/d0/t5: clean tick call, then a reflection call at the SAME key, then the action
    log.call("a2", 0, 5, "tick", 400, 50, 220)  # 100 + 50 = 150 qi
    log.call("a2", 0, 5, "reflection", 300, 100, 160)  # 75 + 100 = 175 qi
    log.action("a2", 0, 5, 30, type="note", text="hm")
    log.day_started(1)
    # a2's only day-1 activity is a spent slot (no LLM_CALL)
    log.action("a2", 1, 1, 0, type="rest", degraded=True, reason="spent", free=True)
    # a1/d1/t2: corrupt-shaped usage fields and a zero bill
    log.add(
        EventKind.LLM_CALL,
        day=1,
        tick=2,
        actor="a1",
        payload={"purpose": "tick", "max_tokens": 220, "usage_in": "abc", "usage_out": None},
        qi_delta=0,
    )
    log.action("a1", 1, 2, 20, type="meditate")
    out = fold_truncation(log.events)
    tick = out["by_purpose"]["tick"]
    assert (tick["calls"], tick["truncated"], tick["truncated_permille"]) == (5, 2, 400)
    # non-truncated usage_out sample [0, 50, 219]: p50 = rank 2, p90 = rank 3
    assert (tick["usage_out_p50"], tick["usage_out_p90"], tick["usage_out_max"]) == (50, 219, 219)
    reflection = out["by_purpose"]["reflection"]
    assert (reflection["calls"], reflection["truncated"], reflection["usage_out_p50"]) == (
        1,
        0,
        100,
    )
    assert out["slots"] == {
        "actions": 6,
        "actions_no_call": 2,
        "actions_single_call": 3,
        "actions_with_retry": 1,
        "retry_permille": 166,  # 1/6 = 166.67
        "actions_with_retry_after_truncation": 0,
        "malformed_forfeits": 1,
    }
    assert out["qi"] == {
        "qi_thinking_total": 319 + 345 + 421 + 150 + 175 + 0,
        "truncated_then_retried_calls": 0,
        "qi_on_truncated_first_calls": 0,
        "share_permille": 0,
        "qi_on_retry_calls": 345,
    }
    a1, a2 = out["agents"]
    assert (a1["calls"], a1["usage_in"], a1["mean_usage_in"]) == (3, 900, 300)
    assert (a1["tick_calls"], a1["usage_in_tick"], a1["mean_usage_in_tick"]) == (3, 900, 300)
    assert (a1["qi_thinking"], a1["qi_spent"]) == (664, 664 + 10 + 20)
    assert (a1["days_active"], a1["qi_per_day"]) == (2, 347)
    assert (a2["calls"], a2["usage_in"], a2["mean_usage_in"]) == (3, 1500, 500)
    assert (a2["tick_calls"], a2["usage_in_tick"], a2["mean_usage_in_tick"]) == (2, 1200, 600)
    assert (a2["qi_thinking"], a2["qi_spent"]) == (746, 746 + 50 + 30)
    assert (a2["days_active"], a2["qi_per_day"]) == (2, 413)


def test_write_retread_needs_nothing_but_the_event_log(
    scripted_run: tuple[Path, Any, list[EventRecord]], tmp_path: Path
) -> None:
    """A run dir holding ONLY events.sqlite3 (no config.toml, no llm_texts,
    no report) yields the byte-identical retread.json."""
    run_dir, _, _ = scripted_run
    bare = tmp_path / "bare"
    bare.mkdir()
    shutil.copyfile(run_dir / "events.sqlite3", bare / "events.sqlite3")
    assert sorted(p.name for p in bare.iterdir()) == ["events.sqlite3"]
    full = write_retread(run_dir, out_path=tmp_path / "full.json").read_bytes()
    assert write_retread(bare, out_path=tmp_path / "bare.json").read_bytes() == full
    assert sorted(p.name for p in bare.iterdir()) == ["events.sqlite3"]  # no side files linger


@pytest.mark.skipif(not ACCEPT2_LOG.exists(), reason="accepted Phase-1 run not present")
def test_accepted_run_pins(tmp_path: Path) -> None:
    """Exact Phase-1 baseline numbers (pinned 2026-09-18; devlog 002 errata).
    These are the retread and truncation baselines Phase 2 measures against."""
    db = tmp_path / "events.sqlite3"
    shutil.copy(ACCEPT2_LOG, db)
    out = json.loads(write_retread(tmp_path, out_path=tmp_path / "r.json").read_text())
    pop = out["retread"]["population"]
    assert (pop["steps"], pop["novel"], pop["retread"], pop["retread_permille"]) == (
        1980,
        770,
        1210,
        611,
    )
    assert (pop["retread_slag"], pop["retread_recipe"]) == (1161, 49)
    assert (pop["attempts"], pop["attempts_empty"], pop["attempts_zero_novel"]) == (980, 108, 508)
    top = out["retread"]["known_slag_retries_top"][0]
    assert top == {"agent_id": "a5", "count": 35, "pair": ["earth", "lacquer-salt"]}
    tick = out["truncation"]["by_purpose"]["tick"]
    assert (tick["calls"], tick["truncated"], tick["truncated_permille"]) == (3723, 1992, 535)
    refl = out["truncation"]["by_purpose"]["reflection"]
    assert (refl["calls"], refl["truncated"], refl["truncated_permille"]) == (240, 233, 970)
    slots = out["truncation"]["slots"]
    assert (slots["actions"], slots["actions_with_retry"], slots["retry_permille"]) == (
        1920,
        1803,
        939,
    )
    assert (slots["actions_with_retry_after_truncation"], slots["malformed_forfeits"]) == (
        1801,
        272,
    )
    qi = out["truncation"]["qi"]
    assert (qi["qi_thinking_total"], qi["qi_on_truncated_first_calls"], qi["share_permille"]) == (
        4933921,
        2311267,
        468,
    )
