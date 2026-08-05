"""Tests for lamarck.analysis.report — the deterministic run-report fold.

Everything in report.json must be re-derivable from the event log alone;
these tests re-derive the load-bearing tables (discoveries, token totals,
per-agent stats) independently and byte-compare two successive writes
(report.json AND report.md) to prove the fold is pure.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from lamarck.analysis import write_report
from lamarck.contracts import EventKind
from lamarck.engine import fold_balances, load_live_config
from lamarck.eventstore import EventStore
from lamarck.live import run_live
from lamarck.serving import ScriptedBackend
from tests.scripted_llm import make as make_scripted

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"


@pytest.fixture(scope="module")
def report_run(tmp_path_factory):
    """A small scripted live run: (cfg, run_dir, summary, events, report)."""
    cfg = load_live_config(VALLEY_TOML)
    cfg = cfg.model_copy(
        update={
            "world": cfg.world.model_copy(update={"days": 2, "rounds_per_day": 4}),
            "model": cfg.model.model_copy(update={"backend": "scripted"}),
            "population": cfg.population.model_copy(update={"founders": 3}),
        }
    )
    run_dir = tmp_path_factory.mktemp("report") / "run"
    summary = run_live(
        cfg,
        run_dir,
        config_path=None,
        backend=ScriptedBackend(make_scripted()),
        difftest_interval=0,
    )
    with EventStore(run_dir / "events.sqlite3") as store:
        events = list(store.scan())
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    return cfg, run_dir, summary, events, report


def test_two_writes_are_byte_identical(report_run):
    _, run_dir, _, _, _ = report_run
    json_before = (run_dir / "report.json").read_bytes()  # written by run_live itself
    md_before = (run_dir / "report.md").read_bytes()
    returned = write_report(run_dir)
    assert returned == run_dir / "report.json"
    assert (run_dir / "report.json").read_bytes() == json_before
    assert (run_dir / "report.md").read_bytes() == md_before


def test_header(report_run):
    cfg, _, summary, _, report = report_run
    assert report["run_id"] == summary.run_id
    assert report["mode"] == "live"
    assert report["model_id"] == cfg.model.model_id
    assert report["days"] == summary.days_elapsed == 2


def test_discoveries_table_matches_task_attempts(report_run):
    _, _, summary, events, report = report_run
    names = {
        ev.payload["agent_id"]: ev.payload["name"]
        for ev in events
        if ev.kind is EventKind.AGENT_SPAWNED
    }
    expected = [
        {
            "day": ev.day,
            "agent_id": ev.actor,
            "name": names[ev.actor],
            "task_id": claim["task_id"],
            "tier": claim["tier"],
            "first_in_world": claim["first"],
        }
        for ev in events
        if ev.kind is EventKind.TASK_ATTEMPT
        for claim in ev.payload["claims"]
    ]
    assert report["discoveries"] == expected
    assert len(expected) == summary.discoveries
    distinct = {row["task_id"] for row in expected}
    assert report["distinct_verified"] == len(distinct) == summary.distinct_discoveries
    tiers = Counter()
    seen = set()
    for row in expected:
        if row["task_id"] not in seen:
            seen.add(row["task_id"])
            tiers[str(row["tier"])] += 1
    assert report["tier_histogram"] == dict(tiers)


def test_token_totals_match_llm_call_sums(report_run):
    _, _, summary, events, report = report_run
    llm = [ev for ev in events if ev.kind is EventKind.LLM_CALL]
    totals = report["totals"]
    assert totals["llm_calls"] == len(llm) == summary.llm_calls
    assert totals["tokens_in"] == sum(ev.payload["usage_in"] for ev in llm)
    assert totals["tokens_in"] == summary.usage_in_total
    assert totals["tokens_out"] == sum(ev.payload["usage_out"] for ev in llm)
    assert totals["tokens_out"] == summary.usage_out_total
    assert totals["qi_thinking"] == sum(-ev.qi_delta for ev in llm)
    assert totals["qi_surcharges"] == sum(
        -ev.qi_delta for ev in events if ev.kind is EventKind.ACTION and ev.qi_delta < 0
    )


def test_per_agent_stats_match_events(report_run):
    cfg, _, _, events, report = report_run
    agents = report["agents"]
    assert [a["agent_id"] for a in agents] == ["a1", "a2", "a3"]  # spawn order
    assert [a["name"] for a in agents] == ["Yan Hua", "Bo Shan", "Mei Lin"]
    balances = fold_balances(events, cfg)
    for entry in agents:
        aid = entry["agent_id"]
        mine = [ev for ev in events if ev.actor == aid]
        actions = [ev for ev in mine if ev.kind is EventKind.ACTION]
        assert entry["qi_remaining"] == balances.qi[aid]
        assert entry["stones"] == balances.stones[aid]
        assert entry["alive"] == balances.alive[aid]
        assert entry["qi_spent"] == sum(-ev.qi_delta for ev in mine if ev.qi_delta < 0)
        assert entry["actions"] == dict(Counter(ev.payload["type"] for ev in actions))
        assert entry["degraded"] == sum(1 for ev in actions if ev.payload.get("degraded"))
        assert entry["malformed"] == sum(
            1 for ev in actions if ev.payload.get("reason") == "malformed"
        )
        assert entry["utterances"] == sum(
            1
            for ev in actions
            if ev.payload["type"] == "converse" and not ev.payload.get("degraded")
        )
        assert entry["notes_written"] == sum(
            1 for ev in actions if ev.payload["type"] == "note" and not ev.payload.get("degraded")
        )
        llm = [ev for ev in mine if ev.kind is EventKind.LLM_CALL]
        assert entry["tokens_in"] == sum(ev.payload["usage_in"] for ev in llm)
        assert entry["tokens_out"] == sum(ev.payload["usage_out"] for ev in llm)


def test_report_values_are_ints_and_strings_only(report_run):
    _, _, _, _, report = report_run

    def check(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                assert isinstance(key, str)
                check(item, f"{path}.{key}")
        elif isinstance(value, list):
            for i, item in enumerate(value):
                check(item, f"{path}[{i}]")
        else:
            assert isinstance(value, (str, int, bool)), f"non-int/str at {path}: {value!r}"

    check(report, "report")


def test_markdown_mirrors_the_fold(report_run):
    _, run_dir, summary, _, report = report_run
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert f"# lamarck run {summary.run_id}" in md
    assert "## Discoveries" in md
    assert "## Agents" in md
    for row in report["discoveries"]:
        assert row["task_id"] in md
    assert "Yan Hua (a1)" in md
