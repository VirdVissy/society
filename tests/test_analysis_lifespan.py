"""Tests for lamarck.analysis.lifespan — the lifespan schedule simulator.

Three layers, mirroring the module's purity claims:

- hand-built synthetic EventRecord lists and spend tables (every number
  below is traced by hand from the rules in the module docstring — the fold
  never needs a real hash, so ``hash="0" * 64`` throughout);
- a determinism test over a small scripted live run (two writes of
  ``lifespan.json`` are byte-identical; the file holds ints/strings/bools/
  null only);
- a structural test over the accepted Phase-1 run when it is present
  locally (nothing pinned yet: the coordinator pins after review).
"""

import json
import random
from pathlib import Path
from typing import Any

import pytest

from lamarck.analysis.lifespan import canary_check, simulate, spend_table, write_lifespan
from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import EventKind, EventRecord
from lamarck.engine import load_live_config
from lamarck.eventstore import EventStore
from lamarck.live import run_live
from lamarck.serving import ScriptedBackend
from tests.scripted_llm import make as make_scripted

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"
ACCEPT_RUN = REPO_ROOT / "runs" / "phase1-accept2"
ACCEPT_LOG = ACCEPT_RUN / "events.sqlite3"

TOP_LEVEL_KEYS = {"run_id", "days", "spend_summary", "scenarios"}


# ----------------------------------------------------------------- helpers


def _ev(seq, day, kind, actor="", payload=None, qi_delta=0, tick=0):
    return EventRecord(
        seq=seq,
        hash="0" * 64,
        day=day,
        tick=tick,
        kind=kind,
        actor=actor,
        payload=payload or {},
        qi_delta=qi_delta,
        stones_delta=0,
    )


def _spawn(seq, agent_id, day=0):
    return _ev(
        seq,
        day,
        EventKind.AGENT_SPAWNED,
        payload={"agent_id": agent_id, "qi_max": 100, "starting_stones": 0, "name": agent_id},
    )


def _assert_json_scalars(value, path="root"):
    """Ints, strings, bools and null only — never floats — at every leaf."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str), f"non-str key at {path}"
            _assert_json_scalars(item, f"{path}.{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _assert_json_scalars(item, f"{path}[{i}]")
    else:
        assert value is None or isinstance(value, (str, int, bool)), f"{path}: {value!r}"
        assert not isinstance(value, float)


def _members(sim, lineage):
    (entry,) = [ln for ln in sim["lineages"] if ln["lineage"] == lineage]
    return [(m["generation"], m["birth_day"], m["death_day"]) for m in entry["members"]]


def _reached(sim):
    return {
        row["generation"]: (row["first_day_any"], row["first_day_half"])
        for row in sim["population"]["generations_reached"]
    }


def _overlap(sim):
    return {
        row["generation"]: (row["days_both_alive"], row["heir_agent_days_with_teacher"])
        for row in sim["population"]["overlap"]["cross_lineage"]
    }


# ------------------------------------------------------------- spend_table


def test_spend_table_counts_negative_action_and_llm_qi_per_day():
    # Spawn order is b2 then a1 on purpose: the table must follow spawn
    # order, not alphabetical order.
    events = [
        _ev(0, 0, EventKind.RUN_STARTED, payload={"run_id": "synthetic"}),
        _spawn(1, "b2"),
        _spawn(2, "a1"),
        _ev(3, 0, EventKind.DAY_STARTED),
        _ev(4, 0, EventKind.LLM_CALL, "b2", {"purpose": "tick"}, qi_delta=-100),
        _ev(5, 0, EventKind.ACTION, "b2", {"type": "travel"}, qi_delta=-7),
        _ev(6, 0, EventKind.LLM_CALL, "a1", {"purpose": "tick"}, qi_delta=-50),
        _ev(7, 0, EventKind.LEDGER_ADJUST, "a1", {"reason": "bounty"}, qi_delta=-999),  # ignored
        _ev(8, 1, EventKind.DAY_STARTED),
        _ev(9, 1, EventKind.LLM_CALL, "b2", {"purpose": "reflection"}, qi_delta=-30),
        _ev(10, 1, EventKind.ACTION, "a1", {"type": "rest"}, qi_delta=0),  # zero: no spend
        _ev(11, 1, EventKind.ACTION, "a1", {"type": "rest"}, qi_delta=5),  # positive: ignored
        _ev(12, 2, EventKind.DAY_STARTED),  # a day nobody spends on
        _ev(13, 2, EventKind.RUN_FINISHED, payload={"days_elapsed": 3}),
    ]
    table = spend_table(events)
    assert list(table) == ["b2", "a1"]
    assert table == {"b2": [107, 30, 0], "a1": [50, 0, 0]}


def test_spend_table_without_day_started_is_empty_series():
    assert spend_table([_spawn(0, "a1")]) == {"a1": []}
    assert spend_table([]) == {}


def test_spend_table_rejects_spend_by_unspawned_actor():
    events = [
        _ev(0, 0, EventKind.DAY_STARTED),
        _ev(1, 0, EventKind.LLM_CALL, "ghost", {"purpose": "tick"}, qi_delta=-1),
    ]
    with pytest.raises(LamarckAssertionError):
        spend_table(events)


# ---------------------------------------------------------------- simulate

# Case 1 — two lineages, no stagger, qi_max 7, 10 days.
#   a spends 3/day: 7 -> 4 -> 1 -> -2  dies dusk of day 2; successor born day 3
#     gen2 dies day 5, gen3 born 6 dies day 8, gen4 born 9 (qi 4 at the end).
#   b spends 2/day: 7 -> 5 -> 3 -> 1 -> -1 dies day 3; gen2 born 4 dies 7,
#     gen3 born 8 alive (qi 3 at the end).
SPEND_1 = {"a": [3, 3, 3], "b": [2, 2, 2, 2]}


@pytest.fixture(scope="module")
def sim_1():
    return simulate(SPEND_1, 7, 10)


def test_case1_members_and_death_days(sim_1):
    assert _members(sim_1, "a") == [(1, 0, 2), (2, 3, 5), (3, 6, 8), (4, 9, None)]
    assert _members(sim_1, "b") == [(1, 0, 3), (2, 4, 7), (3, 8, None)]
    a_members = sim_1["lineages"][0]["members"]
    assert [m["start_qi"] for m in a_members] == [7, 7, 7, 7]
    assert [m["qi_remaining"] for m in a_members] == [-2, -2, -2, 4]
    assert [ln["lineage"] for ln in sim_1["lineages"]] == ["a", "b"]  # spawn order
    assert [ln["series_len"] for ln in sim_1["lineages"]] == [3, 4]
    assert sim_1["stagger"] is None
    assert sim_1["stagger_permille"] == [1000, 1000]


def test_case1_population(sim_1):
    pop = sim_1["population"]
    assert pop["deaths_by_day"] == [0, 0, 1, 1, 0, 1, 0, 1, 1, 0]
    assert pop["generations"] == [
        {"generation": 1, "born": 2, "deaths": 2, "first_death_day": 2, "last_death_day": 3},
        {"generation": 2, "born": 2, "deaths": 2, "first_death_day": 5, "last_death_day": 7},
        {"generation": 3, "born": 2, "deaths": 1, "first_death_day": 8, "last_death_day": 8},
        {"generation": 4, "born": 1, "deaths": 0, "first_death_day": None, "last_death_day": None},
    ]
    assert pop["spread"] == 1  # 3 - 2
    assert pop["survivors"] == 2  # a gen4, b gen3
    assert pop["half_threshold"] == 1  # ceil(2 / 2)
    assert _reached(sim_1) == {1: (0, 0), 2: (3, 3), 3: (6, 6), 4: (9, 9)}


def test_case1_overlap(sim_1):
    # gen1 alive: a 0-2, b 0-3; gen2 alive: a 3-5, b 4-7; gen3: a 6-8, b 8-9;
    # gen4: a 9.  Both alive (1,2): day 3 only (b gen1 + a gen2).
    # (2,3): days 6, 7 (b gen2 + a gen3).  (3,4): day 9 (b gen3 + a gen4).
    assert _overlap(sim_1) == {1: (1, 1), 2: (2, 2), 3: (1, 1)}
    overlap = sim_1["population"]["overlap"]
    assert overlap["same_lineage"] == [
        {"lineage": "a", "teacher_heir_days": 0},
        {"lineage": "b", "teacher_heir_days": 0},
    ]
    assert overlap["same_lineage_total"] == 0


# Case 2 — three lineages (half threshold 2), qi_max 10, 8 days.
#   a spends 5: dies 1, 3, 5, 7 (gen1..gen4; no gen5: day 8 is past the end)
#   b spends 4: 10 -> 6 -> 2 -> -2 dies day 2; gen2 born 3 dies 5; gen3 born 6
#   c spends 2: dies day 4; gen2 born 5 survives (qi 4)
SPEND_2 = {"a": [5], "b": [4], "c": [2]}


@pytest.fixture(scope="module")
def sim_2():
    return simulate(SPEND_2, 10, 8)


def test_case2_members(sim_2):
    assert _members(sim_2, "a") == [(1, 0, 1), (2, 2, 3), (3, 4, 5), (4, 6, 7)]
    assert _members(sim_2, "b") == [(1, 0, 2), (2, 3, 5), (3, 6, None)]
    assert _members(sim_2, "c") == [(1, 0, 4), (2, 5, None)]
    pop = sim_2["population"]
    assert pop["deaths_by_day"] == [0, 1, 1, 1, 1, 2, 0, 1]
    assert pop["spread"] == 3  # founders die on days 1 and 4
    assert pop["survivors"] == 2  # b gen3, c gen2 (a's gen4 died at the last dusk)
    assert pop["generations"][3] == {
        "generation": 4,
        "born": 1,
        "deaths": 1,
        "first_death_day": 7,
        "last_death_day": 7,
    }


def test_case2_half_of_lineages_rule(sim_2):
    # gen>=2 living members per day: day2 {a}, day3 {a, b} -> half on day 3.
    # gen>=3: day4 {a}, day5 {a}, day6 {a(gen4), b} -> half on day 6.
    # gen>=4: only a ever -> never half.
    assert sim_2["population"]["half_threshold"] == 2
    assert _reached(sim_2) == {1: (0, 0), 2: (2, 3), 3: (4, 6), 4: (6, None)}


def test_case2_overlap(sim_2):
    # gen1 alive: a 0-1, b 0-2, c 0-4; gen2: a 2-3, b 3-5, c 5-7;
    # gen3: a 4-5, b 6-7; gen4: a 6-7.
    # (1,2): days 2, 3, 4; heir-days: a-gen2 {2,3} + b-gen2 {3,4} = 4
    # (2,3): days 4-7; heir-days: a-gen3 {4,5} + b-gen3 {6,7} = 4
    # (3,4): days 6, 7; heir-days: a-gen4 {6,7} = 2
    assert _overlap(sim_2) == {1: (3, 4), 2: (4, 4), 3: (2, 2)}


def test_no_replacement_ends_every_lineage_at_its_founder():
    sim = simulate(SPEND_2, 10, 8, replacement=False)
    assert sim["replacement"] is False
    assert _members(sim, "a") == [(1, 0, 1)]
    assert _members(sim, "b") == [(1, 0, 2)]
    assert _members(sim, "c") == [(1, 0, 4)]
    pop = sim["population"]
    assert pop["deaths_by_day"] == [0, 1, 1, 0, 1, 0, 0, 0]
    assert pop["generations"] == [
        {"generation": 1, "born": 3, "deaths": 3, "first_death_day": 1, "last_death_day": 4}
    ]
    assert _reached(sim) == {1: (0, 0)}
    assert pop["overlap"]["cross_lineage"] == []
    assert pop["survivors"] == 0


def test_age_aligned_recycling_cycles_the_founder_series():
    # Series [2, 2, 5], qi_max 4. Founder: 2, 0 -> dies day 1. Its successor is
    # born on day 2 and spends by ITS OWN age: 2 (age 0) then 2 (age 1) -> 0 ->
    # dies day 3. Calendar-aligned spend would charge series[2] = 5 on day 2
    # and kill it on day 2 instead. Generation 3 (born day 4) repeats: dies 5.
    sim = simulate({"a": [2, 2, 5]}, 4, 6)
    assert _members(sim, "a") == [(1, 0, 1), (2, 2, 3), (3, 4, 5)]


def test_stagger_explicit_permille_list_floors_start_qi():
    # a full (10), b half (5), c 333 permille -> floor(3.33) = 3.
    sim = simulate({"a": [4], "b": [4], "c": [4]}, 10, 5, stagger=[1000, 500, 333])
    starts = {ln["lineage"]: ln["members"][0]["start_qi"] for ln in sim["lineages"]}
    assert starts == {"a": 10, "b": 5, "c": 3}
    assert sim["stagger"] == [1000, 500, 333]
    assert sim["stagger_permille"] == [1000, 500, 333]
    assert [ln["stagger_permille"] for ln in sim["lineages"]] == [1000, 500, 333]
    # a: 6, 2, -2 dies day 2; b: 1, -3 dies day 1; c: -1 dies day 0.
    assert _members(sim, "a")[0] == (1, 0, 2)
    assert _members(sim, "b")[0] == (1, 0, 1)
    assert _members(sim, "c")[0] == (1, 0, 0)
    # Successors always start full, whatever the founder's stagger.
    assert all(m["start_qi"] == 10 for ln in sim["lineages"] for m in ln["members"][1:])
    assert sim["population"]["spread"] == 2


def test_uniform_stagger_is_deterministic_and_matches_random_random():
    spend = {f"a{i}": [3] for i in range(1, 9)}
    first = simulate(spend, 10, 12, stagger=("uniform", 600, 1000), stagger_seed=1)
    second = simulate(spend, 10, 12, stagger=("uniform", 600, 1000), stagger_seed=1)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    rng = random.Random(1)
    expected = [rng.randint(600, 1000) for _ in range(8)]
    assert first["stagger_permille"] == expected
    assert all(600 <= f <= 1000 for f in expected)
    assert first["stagger"] == ["uniform", 600, 1000]
    assert first["stagger_seed"] == 1
    starts = [ln["members"][0]["start_qi"] for ln in first["lineages"]]
    assert starts == [10 * f // 1000 for f in expected]
    other = simulate(spend, 10, 12, stagger=("uniform", 600, 1000), stagger_seed=2)
    assert other["stagger_permille"] != expected


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"stagger": ("uniform", 600, 1000)}, "requires stagger_seed"),
        ({"stagger": ("gaussian", 600, 1000), "stagger_seed": 1}, "unknown stagger scheme"),
        ({"stagger": ("uniform", 0, 1000), "stagger_seed": 1}, "1 <= lo <= hi <= 1000"),
        ({"stagger": ("uniform", 900, 800), "stagger_seed": 1}, "1 <= lo <= hi <= 1000"),
        ({"stagger": [1000]}, "entries for 2 founders"),
        ({"stagger": [1000, 0]}, "outside 1..1000"),
        ({"stagger": [1000, 1001]}, "outside 1..1000"),
    ],
)
def test_bad_stagger_specs_are_rejected(kwargs, message):
    with pytest.raises(ValueError, match=message):
        simulate({"a": [1], "b": [1]}, 10, 3, **kwargs)


def test_bad_inputs_are_rejected():
    with pytest.raises(ValueError, match="empty"):
        simulate({}, 10, 3)
    with pytest.raises(ValueError, match="empty"):
        simulate({"a": []}, 10, 3)
    with pytest.raises(ValueError, match="negative"):
        simulate({"a": [1, -1]}, 10, 3)
    with pytest.raises(ValueError, match="qi_max"):
        simulate({"a": [1]}, 0, 3)
    with pytest.raises(ValueError, match="days"):
        simulate({"a": [1]}, 10, 0)


def test_simulate_output_is_json_scalars_only(sim_1, sim_2):
    _assert_json_scalars(sim_1)
    _assert_json_scalars(sim_2)


# ------------------------------------------------------------ canary_check


def test_canary_passes_when_a_death_and_a_successor_fit_the_arm(sim_1):
    # Arm of 4 days = days 0..3: deaths on days 2 and 3; a's successor is born
    # on day 3 and acts exactly 1 day inside the arm.
    check = canary_check(sim_1, 4)
    assert check == {
        "arm_days": 4,
        "min_deaths": 1,
        "min_generation": 2,
        "deaths_within_arm": 2,
        "successors_born": 1,
        "successors_acting_days": 1,
        "generation_reached_by_arm_end": 2,
        "generation_reached_by_half_by_arm_end": 2,
        "passes": True,
        "reasons": [],
    }


def test_canary_reports_every_failed_bar(sim_1):
    short = canary_check(sim_1, 2)  # days 0..1: nobody dies yet
    assert short["passes"] is False
    assert short["deaths_within_arm"] == 0
    assert short["generation_reached_by_arm_end"] == 1
    assert short["reasons"] == [
        "deaths_within_arm 0 < min_deaths 1",
        "generation_reached_by_arm_end 1 < min_generation 2",
    ]
    three = canary_check(sim_1, 3)  # a dies on day 2; its heir arrives on day 3
    assert three["deaths_within_arm"] == 1
    assert three["successors_born"] == 0
    assert three["successors_acting_days"] == 0
    assert three["reasons"] == ["generation_reached_by_arm_end 1 < min_generation 2"]
    assert canary_check(sim_1, 3, min_generation=1)["passes"] is True
    assert canary_check(sim_1, 4, min_deaths=3)["reasons"] == ["deaths_within_arm 2 < min_deaths 3"]


def test_canary_counts_successor_acting_days_inside_the_arm(sim_1, sim_2):
    # Whole horizon of case 1 (10 days): successors a-gen2 (3..5) 3 days,
    # a-gen3 (6..8) 3, a-gen4 (9) 1, b-gen2 (4..7) 4, b-gen3 (8..9) 2 -> 13.
    full = canary_check(sim_1, 10)
    assert full["successors_born"] == 5
    assert full["successors_acting_days"] == 13
    assert full["deaths_within_arm"] == 5
    assert full["generation_reached_by_arm_end"] == 4
    # Case 2, arm 5 (days 0..4): successors a-gen2 (2..3) 2, a-gen3 (4..5 -> 4)
    # 1, b-gen2 (3..5 -> 3..4) 2 -> 5; gen3 born day 4 counts; half only gen2.
    arm = canary_check(sim_2, 5)
    assert arm["successors_born"] == 3
    assert arm["successors_acting_days"] == 5
    assert arm["generation_reached_by_arm_end"] == 3
    assert arm["generation_reached_by_half_by_arm_end"] == 2


def test_canary_rejects_arms_outside_the_simulation(sim_1):
    with pytest.raises(ValueError, match="arm_days"):
        canary_check(sim_1, 0)
    with pytest.raises(ValueError, match="arm_days"):
        canary_check(sim_1, 11)


# -------------------------------------------------- scripted-run determinism


@pytest.fixture(scope="module")
def scripted_run(tmp_path_factory):
    """A small scripted live run: (run_dir, summary, events)."""
    cfg = load_live_config(VALLEY_TOML)
    cfg = cfg.model_copy(
        update={
            "world": cfg.world.model_copy(update={"days": 2, "rounds_per_day": 4}),
            "model": cfg.model.model_copy(update={"backend": "scripted"}),
            "population": cfg.population.model_copy(update={"founders": 3}),
        }
    )
    run_dir = tmp_path_factory.mktemp("lifespan") / "run"
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


def test_write_lifespan_is_byte_identical_and_well_formed(scripted_run, tmp_path):
    run_dir, summary, events = scripted_run
    first = write_lifespan(
        run_dir, [3000, 5000], 6, stagger=("uniform", 600, 1000), stagger_seed=1, arm_days=3
    )
    assert first == run_dir / "lifespan.json"
    before = first.read_bytes()
    again = write_lifespan(
        run_dir,
        [3000, 5000],
        6,
        stagger=("uniform", 600, 1000),
        stagger_seed=1,
        arm_days=3,
        out_path=tmp_path / "elsewhere.json",
    )
    assert again == tmp_path / "elsewhere.json"
    assert again.read_bytes() == before
    third = write_lifespan(
        run_dir, [3000, 5000], 6, stagger=("uniform", 600, 1000), stagger_seed=1, arm_days=3
    )
    assert third == first
    assert third.read_bytes() == before

    data = json.loads(before.decode("utf-8"))
    assert set(data) == TOP_LEVEL_KEYS
    assert data["run_id"] == summary.run_id
    assert data["days"] == 6
    _assert_json_scalars(data)

    # The spend summary re-derives from the events independently.
    table = spend_table(events)
    assert list(table) == ["a1", "a2", "a3"]
    assert all(len(series) == 2 for series in table.values())
    for aid, series in table.items():
        mine = [
            ev
            for ev in events
            if ev.actor == aid
            and ev.kind in (EventKind.ACTION, EventKind.LLM_CALL)
            and ev.qi_delta < 0
        ]
        assert series == [sum(-ev.qi_delta for ev in mine if ev.day == d) for d in (0, 1)]
        assert sum(series) > 0
    summary_block = data["spend_summary"]
    assert summary_block["agents"] == ["a1", "a2", "a3"]
    assert summary_block["measured_days"] == 2
    assert summary_block["per_agent_total"] == {aid: sum(s) for aid, s in table.items()}
    assert summary_block["per_agent_mean"] == {aid: sum(s) // 2 for aid, s in table.items()}
    # Lower median of two values is the smaller one.
    assert summary_block["per_agent_median"] == {aid: min(s) for aid, s in table.items()}
    everything = sorted(v for s in table.values() for v in s)
    assert summary_block["all_days_median"] == everything[(len(everything) - 1) // 2]
    assert summary_block["all_days_mean"] == sum(everything) // len(everything)
    assert summary_block["all_days_min"] == everything[0]
    assert summary_block["all_days_max"] == everything[-1]

    # Scenarios: one simulate() result per qi_max, in order, plus a canary.
    assert [s["qi_max"] for s in data["scenarios"]] == [3000, 5000]
    for scenario in data["scenarios"]:
        assert scenario["days"] == 6
        assert scenario["stagger"] == ["uniform", 600, 1000]
        assert scenario["stagger_seed"] == 1
        assert scenario["canary"]["arm_days"] == 3
        assert len(scenario["population"]["deaths_by_day"]) == 6
        expected = simulate(
            table, scenario["qi_max"], 6, stagger=("uniform", 600, 1000), stagger_seed=1
        )
        expected["canary"] = canary_check(expected, 3)
        assert scenario == expected


def test_write_lifespan_defaults(scripted_run, tmp_path):
    run_dir, _, _ = scripted_run
    path = write_lifespan(run_dir, [4000], 3, out_path=tmp_path / "plain.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    (scenario,) = data["scenarios"]
    assert scenario["stagger"] is None
    assert scenario["stagger_seed"] is None
    assert scenario["stagger_permille"] == [1000, 1000, 1000]
    assert "canary" not in scenario


# --------------------------------------------------------- the accepted run


@pytest.mark.skipif(
    not (ACCEPT_RUN / "events.sqlite3").exists(), reason="accepted Phase-1 run not present"
)
def test_accepted_run_reproduces_the_population_cliff(tmp_path):
    path = write_lifespan(ACCEPT_RUN, [90000, 200000, 250000], 30, out_path=tmp_path / "l.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    _assert_json_scalars(data)
    assert set(data) == TOP_LEVEL_KEYS
    summary = data["spend_summary"]
    assert len(summary["agents"]) == 8
    assert summary["measured_days"] == 30
    assert summary["all_days_median"] > 0
    assert 0 < summary["all_days_min"] <= summary["all_days_median"] <= summary["all_days_max"]
    for aid in summary["agents"]:
        assert summary["per_agent_mean"][aid] > 0
        assert summary["per_agent_median"][aid] > 0

    by_qi = {s["qi_max"]: s for s in data["scenarios"]}
    cliff = by_qi[200000]["population"]
    founders = cliff["generations"][0]
    assert founders["deaths"] == 8
    assert 8 <= founders["first_death_day"] <= 12
    assert cliff["spread"] <= 3  # the known cliff: every founder inside a few days
    assert sum(cliff["deaths_by_day"]) == sum(g["deaths"] for g in cliff["generations"])
    assert cliff["overlap"]["same_lineage_total"] == 0
    for row in cliff["generations_reached"]:
        assert row["first_day_any"] is not None
    for scenario in data["scenarios"]:
        assert all(1 <= f <= 1000 for f in scenario["stagger_permille"])
        assert len(scenario["lineages"]) == 8
        for lineage in scenario["lineages"]:
            assert lineage["series_len"] == 30
    # The 90k life is shorter than the 250k life.
    first_death = {q: by_qi[q]["population"]["generations"][0]["first_death_day"] for q in by_qi}
    assert first_death[90000] < first_death[200000] < first_death[250000]


# ------------------------------------------------- reviewer's adversarial cases


def test_exact_zero_qi_is_a_death_and_the_final_dusk_leaves_no_successor():
    # Series [5], qi_max 10: 10 -> 5 -> 0 — qi lands EXACTLY on zero at the
    # dusk of day 1, and ``qi <= 0`` is a death, not a survival. Over 4 days:
    # gen1 dies day 1 (qi 0); gen2 born day 2 dies day 3 (qi 0) — the FINAL
    # dusk, so no successor follows (day 4 is past the end) and nobody
    # survives.
    sim = simulate({"a": [5]}, 10, 4)
    assert _members(sim, "a") == [(1, 0, 1), (2, 2, 3)]
    assert [m["qi_remaining"] for m in sim["lineages"][0]["members"]] == [0, 0]
    pop = sim["population"]
    assert pop["deaths_by_day"] == [0, 1, 0, 1]
    assert pop["survivors"] == 0
    assert pop["spread"] == 0
    assert pop["generations"] == [
        {"generation": 1, "born": 1, "deaths": 1, "first_death_day": 1, "last_death_day": 1},
        {"generation": 2, "born": 1, "deaths": 1, "first_death_day": 3, "last_death_day": 3},
    ]
    assert _reached(sim) == {1: (0, 0), 2: (2, 2)}
    assert _overlap(sim) == {1: (0, 0)}
    full = canary_check(sim, 4)
    assert (full["deaths_within_arm"], full["successors_born"]) == (2, 1)
    assert full["successors_acting_days"] == 2  # the heir acted on days 2..3
    assert full["passes"] is True

    # Series [10] drains full qi every day: every member lives exactly its
    # birth day. A 2-day arm sees the heir born on the arm's LAST day and
    # dying at that very dusk — it still counts as acting 1 day.
    daily = simulate({"a": [10]}, 10, 3)
    assert _members(daily, "a") == [(1, 0, 0), (2, 1, 1), (3, 2, 2)]
    two = canary_check(daily, 2)
    assert (two["deaths_within_arm"], two["successors_born"]) == (2, 1)
    assert two["successors_acting_days"] == 1
    assert two["generation_reached_by_arm_end"] == 2
    assert two["passes"] is True
    one = canary_check(daily, 1)
    assert (one["deaths_within_arm"], one["successors_born"]) == (1, 0)
    assert one["successors_acting_days"] == 0
    assert one["reasons"] == ["generation_reached_by_arm_end 1 < min_generation 2"]


def test_nobody_dies_when_spend_never_drains_qi():
    # A zero-spend series and a series that cannot drain qi_max inside the
    # horizon: no death, no successor, a null spread, no cross-lineage rows —
    # and a canary that fails BOTH bars without choking on the empty data.
    # ``simulate`` must also leave its input untouched.
    spend = {"idle": [0], "thrifty": [1, 2]}
    frozen = json.dumps(spend)
    sim = simulate(spend, 100, 5)
    assert json.dumps(spend) == frozen
    assert _members(sim, "idle") == [(1, 0, None)]
    assert _members(sim, "thrifty") == [(1, 0, None)]
    # thrifty recycles 1, 2, 1, 2, 1 = 7 over five days.
    assert [ln["members"][0]["qi_remaining"] for ln in sim["lineages"]] == [100, 93]
    pop = sim["population"]
    assert pop["deaths_by_day"] == [0, 0, 0, 0, 0]
    assert pop["spread"] is None
    assert pop["survivors"] == 2
    assert pop["generations"] == [
        {"generation": 1, "born": 2, "deaths": 0, "first_death_day": None, "last_death_day": None}
    ]
    assert _reached(sim) == {1: (0, 0)}
    assert pop["overlap"] == {
        "cross_lineage": [],
        "same_lineage": [
            {"lineage": "idle", "teacher_heir_days": 0},
            {"lineage": "thrifty", "teacher_heir_days": 0},
        ],
        "same_lineage_total": 0,
    }
    _assert_json_scalars(sim)
    check = canary_check(sim, 5)
    assert check["passes"] is False
    assert (check["deaths_within_arm"], check["successors_born"]) == (0, 0)
    assert check["successors_acting_days"] == 0
    assert check["generation_reached_by_arm_end"] == 1
    assert check["generation_reached_by_half_by_arm_end"] == 1
    assert check["reasons"] == [
        "deaths_within_arm 0 < min_deaths 1",
        "generation_reached_by_arm_end 1 < min_generation 2",
    ]


def test_spend_table_late_spawn_gets_leading_zeros_and_out_of_order_day_asserts():
    # An agent spawned on day 1 (the shape of a Phase-2 successor) is indexed
    # from day 0 like everyone else: its series carries a leading 0 that the
    # simulator would recycle as a day-0 spend — a documented caveat, made
    # visible here rather than hidden. Days after a death stay 0 too.
    events = [
        _spawn(0, "a1"),
        _ev(1, 0, EventKind.DAY_STARTED),
        _ev(2, 0, EventKind.LLM_CALL, "a1", {"purpose": "tick"}, qi_delta=-8),
        _ev(3, 1, EventKind.DAY_STARTED),
        _spawn(4, "late", day=1),
        _ev(5, 1, EventKind.ACTION, "late", {"type": "note"}, qi_delta=-3),
        _ev(6, 1, EventKind.AGENT_DIED, "a1", {"cause": "qi_exhausted"}),
        _ev(7, 2, EventKind.DAY_STARTED),
        _ev(8, 2, EventKind.LLM_CALL, "late", {"purpose": "tick"}, qi_delta=-4),
    ]
    assert spend_table(events) == {"a1": [8, 0, 0], "late": [0, 3, 4]}
    # A spend logged on a day whose DAY_STARTED has not been seen is a corrupt
    # log, not a quiet extra column.
    stray = _ev(9, 1, EventKind.ACTION, "a1", {"type": "note"}, qi_delta=-1)
    with pytest.raises(LamarckAssertionError, match="DAY_STARTED"):
        spend_table(events[:3] + [stray])


def test_write_lifespan_refuses_a_run_dir_without_an_event_log(tmp_path):
    # Opening an EventStore on a missing path would CREATE an empty log — a
    # mutation the module must never make. Refuse before touching anything.
    with pytest.raises(FileNotFoundError, match="events.sqlite3"):
        write_lifespan(tmp_path, [100], 3, out_path=tmp_path / "out.json")
    assert sorted(p.name for p in tmp_path.iterdir()) == []


@pytest.mark.skipif(not ACCEPT_LOG.exists(), reason="accepted Phase-1 run not present")
def test_accepted_run_schedule_pins() -> None:
    """Exact schedule numbers from the accepted run's spend (pinned 2026-09-18;
    plan v2 §2.4, D3, Stage E). Founders die within a 2-day window at any
    qi_max without stagger; the ("uniform", 600, 1000) stagger with seed 1
    spreads first deaths over 5 days at 200k."""
    with EventStore(ACCEPT_LOG, readonly=True) as store:
        spend = spend_table(list(store.scan()))
    assert sorted(spend) == [f"a{i}" for i in range(1, 9)]

    def founder_deaths(sim: dict[str, Any]) -> list[int | None]:
        return [lineage["members"][0]["death_day"] for lineage in sim["lineages"]]

    def gen_half(sim: dict[str, Any], g: int) -> int | None:
        rows = {
            row["generation"]: row["first_day_half"]
            for row in sim["population"]["generations_reached"]
        }
        return rows.get(g)

    plain200 = simulate(spend, 200_000, 30)
    assert founder_deaths(plain200) == [11, 9, 11, 9, 10, 9, 10, 10]
    assert gen_half(plain200, 3) == 22
    plain250 = simulate(spend, 250_000, 30)
    assert founder_deaths(plain250) == [13, 11, 13, 12, 12, 12, 12, 12]
    assert gen_half(plain250, 3) == 26
    plain90 = simulate(spend, 90_000, 30)
    assert founder_deaths(plain90) == [5, 4, 5, 5, 5, 4, 4, 5]

    stag200 = simulate(spend, 200_000, 30, stagger=("uniform", 600, 1000), stagger_seed=1)
    assert stag200["stagger_permille"] == [668, 891, 991, 632, 730, 660, 853, 989]
    assert founder_deaths(stag200) == [7, 8, 11, 6, 8, 6, 8, 10]
    assert gen_half(stag200, 3) == 20
    assert canary_check(stag200, 14, min_deaths=1, min_generation=2)["passes"] is True
    stag70 = simulate(spend, 70_000, 30, stagger=("uniform", 600, 1000), stagger_seed=1)
    assert founder_deaths(stag70) == [2, 3, 4, 2, 3, 2, 3, 4]
    smoke = canary_check(stag70, 8, min_deaths=1, min_generation=2)
    assert (smoke["passes"], smoke["deaths_within_arm"], smoke["successors_acting_days"]) == (
        True,
        13,
        33,
    )
