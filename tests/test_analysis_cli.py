"""CLI wiring for the Stage-0 analysis tools (``lamarck exposure|lifespan|retread``).

The commands are thin: they check for ``events.sqlite3``, call the module's
``write_*`` function, and print the output path. A 2-day scripted live run
shared by the module is the fixture; every output must be byte-identical
across two invocations and must never create anything but its own file.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lamarck.cli import app
from lamarck.engine import load_live_config
from lamarck.live import run_live
from lamarck.serving import ScriptedBackend
from tests.scripted_llm import make as make_scripted

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"
runner = CliRunner()


@pytest.fixture(scope="module")
def analysis_run(tmp_path_factory) -> Path:
    cfg = load_live_config(VALLEY_TOML)
    cfg = cfg.model_copy(
        update={
            "world": cfg.world.model_copy(update={"days": 2, "rounds_per_day": 4}),
            "model": cfg.model.model_copy(update={"backend": "scripted"}),
            "population": cfg.population.model_copy(update={"founders": 3}),
        }
    )
    run_dir = tmp_path_factory.mktemp("analysis-cli") / "run"
    run_live(
        cfg,
        run_dir,
        config_path=None,
        backend=ScriptedBackend(make_scripted()),
        difftest_interval=0,
    )
    return run_dir


def _listing(run_dir: Path) -> list[str]:
    return sorted(p.name for p in run_dir.iterdir())


def test_retread_cli_writes_default_and_custom_paths(analysis_run: Path, tmp_path: Path) -> None:
    before = _listing(analysis_run)
    result = runner.invoke(app, ["retread", str(analysis_run)])
    assert result.exit_code == 0, result.output
    default = analysis_run / "retread.json"
    assert default.is_file()
    assert result.output.startswith("wrote")
    assert str(default) in "".join(result.output.split())  # rich wraps long paths
    assert _listing(analysis_run) == sorted([*before, "retread.json"])  # nothing else created
    custom = tmp_path / "r.json"
    assert runner.invoke(app, ["retread", str(analysis_run), "--out", str(custom)]).exit_code == 0
    assert custom.read_bytes() == default.read_bytes()
    data = json.loads(default.read_text())
    assert set(data) == {"run_id", "retread", "truncation"}


def test_lifespan_cli_scenarios_and_stagger(analysis_run: Path, tmp_path: Path) -> None:
    out = tmp_path / "l.json"
    result = runner.invoke(
        app,
        [
            "lifespan",
            str(analysis_run),
            "--qi-max",
            "5000",
            "--qi-max",
            "20000",
            "--days",
            "6",
            "--stagger",
            "uniform:600:1000",
            "--stagger-seed",
            "1",
            "--arm-days",
            "4",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert [s["qi_max"] for s in data["scenarios"]] == [5000, 20000]
    assert all("canary" in s for s in data["scenarios"])
    again = tmp_path / "l2.json"
    runner.invoke(
        app,
        [
            "lifespan",
            str(analysis_run),
            "--qi-max",
            "5000",
            "--qi-max",
            "20000",
            "--days",
            "6",
            "--stagger",
            "uniform:600:1000",
            "--stagger-seed",
            "1",
            "--arm-days",
            "4",
            "--out",
            str(again),
        ],
    )
    assert again.read_bytes() == out.read_bytes()
    explicit = runner.invoke(
        app,
        [
            "lifespan",
            str(analysis_run),
            "--qi-max",
            "5000",
            "--stagger",
            "700,800,900",
            "--out",
            str(tmp_path / "l3.json"),
        ],
    )
    assert explicit.exit_code == 0, explicit.output


def test_analysis_cli_refuses_a_dir_without_a_log(tmp_path: Path) -> None:
    empty = tmp_path / "nolog"
    empty.mkdir()
    for args in (["retread", str(empty)], ["lifespan", str(empty), "--qi-max", "100"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2, result.output
    assert _listing(empty) == []  # nothing was created by the refusal


def test_exposure_cli_rules_and_window(analysis_run: Path, tmp_path: Path) -> None:
    default = tmp_path / "e.json"
    result = runner.invoke(app, ["exposure", str(analysis_run), "--out", str(default)])
    assert result.exit_code == 0, result.output
    data = json.loads(default.read_text())
    assert (data["pair_rule"], data["window_days"]) == ("connected", 2)
    again = tmp_path / "e2.json"
    runner.invoke(app, ["exposure", str(analysis_run), "--out", str(again)])
    assert again.read_bytes() == default.read_bytes()
    sentence = tmp_path / "e3.json"
    result = runner.invoke(
        app,
        [
            "exposure",
            str(analysis_run),
            "--pair-rule",
            "sentence",
            "--window-days",
            "3",
            "--out",
            str(sentence),
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(sentence.read_text())
    assert (data["pair_rule"], data["window_days"]) == ("sentence", 3)
    bad = runner.invoke(app, ["exposure", str(analysis_run), "--pair-rule", "loose"])
    assert bad.exit_code == 2
    assert not (analysis_run / "exposure.json").exists()  # refused before writing anything
