"""CLI acceptance for the Phase-1 commands: `lamarck live`, `lamarck audit`,
`lamarck report`, and mode-dispatching `lamarck replay [--deep]`.

Exit-code contract: 0 ok / 1 verification-or-audit failure / 2 usage
errors. The scripted-backend seam is exercised exactly as documented:
``--scripted-module tests.scripted_llm:make`` names a zero-arg factory; the
flag is REQUIRED for backend="scripted" configs and REFUSED for mlx ones
(so no mlx import can ever hide behind it).
"""

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lamarck.cli import app
from lamarck.eventstore import canonical_bytes

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"
WORLD_TOML = REPO_ROOT / "configs" / "world.toml"

runner = CliRunner()


@pytest.fixture(scope="module")
def scripted_config(tmp_path_factory) -> Path:
    """A tiny scripted-backend live config derived from valley.toml."""
    text = VALLEY_TOML.read_text(encoding="utf-8")
    for old, new in [
        ("days = 30", "days = 2"),
        ("rounds_per_day = 8", "rounds_per_day = 2"),
        ("founders = 8", "founders = 3"),
        ('backend = "mlx"', 'backend = "scripted"'),
    ]:
        assert old in text
        text = text.replace(old, new)
    path = tmp_path_factory.mktemp("cli-cfg") / "tiny-valley.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def cli_live_run(scripted_config, tmp_path_factory):
    """One 2-day scripted live run shared by the module (runs are immutable)."""
    out = tmp_path_factory.mktemp("cli-live") / "run"
    result = runner.invoke(
        app,
        [
            "live",
            "--config",
            str(scripted_config),
            "--out",
            str(out),
            "--scripted-module",
            "tests.scripted_llm:make",
            "--difftest-interval",
            "1",
            "--no-dashboard",
        ],
    )
    assert result.exit_code == 0, result.output
    return out, result


def test_live_run_artifacts(cli_live_run, scripted_config):
    out, result = cli_live_run
    assert "lamarck live run" in result.output
    assert (out / "events.sqlite3").is_file()
    assert (out / "config.toml").read_bytes() == scripted_config.read_bytes()  # verbatim copy
    assert (out / "report.json").is_file()
    assert (out / "report.md").is_file()
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "live"
    assert report["days"] == 2


def test_live_days_override_synthesizes_config(scripted_config, tmp_path):
    out = tmp_path / "override"
    result = runner.invoke(
        app,
        [
            "live",
            "--config",
            str(scripted_config),
            "--out",
            str(out),
            "--days",
            "1",
            "--scripted-module",
            "tests.scripted_llm:make",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "days = 1" in (out / "config.toml").read_text(encoding="utf-8")


def test_live_scripted_backend_requires_seam_flag(scripted_config, tmp_path):
    result = runner.invoke(
        app,
        ["live", "--config", str(scripted_config), "--out", str(tmp_path / "x")],
    )
    assert result.exit_code == 2


def test_live_seam_flag_refused_for_mlx_backend(tmp_path):
    """--scripted-module on an mlx config errors out BEFORE any model (or
    mlx import) could load."""
    result = runner.invoke(
        app,
        [
            "live",
            "--config",
            str(VALLEY_TOML),
            "--out",
            str(tmp_path / "x"),
            "--scripted-module",
            "tests.scripted_llm:make",
        ],
    )
    assert result.exit_code == 2


def test_live_bad_seam_spec_is_usage_error(scripted_config, tmp_path):
    result = runner.invoke(
        app,
        [
            "live",
            "--config",
            str(scripted_config),
            "--out",
            str(tmp_path / "x"),
            "--scripted-module",
            "tests.scripted_llm",  # missing :FACTORY
        ],
    )
    assert result.exit_code == 2


def test_live_refuses_existing_out_dir(scripted_config, tmp_path):
    existing = tmp_path / "occupied"
    existing.mkdir()
    result = runner.invoke(
        app,
        [
            "live",
            "--config",
            str(scripted_config),
            "--out",
            str(existing),
            "--scripted-module",
            "tests.scripted_llm:make",
        ],
    )
    assert result.exit_code == 1


def test_audit_valley_exits_zero(tmp_path):
    result = runner.invoke(app, ["audit", "--config", str(VALLEY_TOML)])
    assert result.exit_code == 0, result.output
    assert "ok: True" in result.output


def test_replay_live_shallow_and_deep(cli_live_run):
    out, _ = cli_live_run
    shallow = runner.invoke(app, ["replay", str(out)])
    assert shallow.exit_code == 0, shallow.output
    assert "replay OK" in shallow.output
    deep = runner.invoke(app, ["replay", str(out), "--deep"])
    assert deep.exit_code == 0, deep.output


def test_replay_tampered_live_run_exits_one(cli_live_run, tmp_path):
    out, _ = cli_live_run
    tampered = tmp_path / "tampered"
    shutil.copytree(out, tampered)
    conn = sqlite3.connect(tampered / "events.sqlite3")
    try:
        seq, payload = conn.execute(
            "SELECT seq, payload FROM events WHERE kind = 'day_started' ORDER BY seq LIMIT 1"
        ).fetchone()
        flipped = canonical_bytes({"day": 999}).decode("utf-8")
        assert flipped != payload
        conn.execute("UPDATE events SET payload = ? WHERE seq = ?", (flipped, seq))
        conn.commit()
    finally:
        conn.close()
    result = runner.invoke(app, ["replay", str(tampered)])
    assert result.exit_code == 1


def test_report_round_trip(cli_live_run):
    out, _ = cli_live_run
    before = (out / "report.json").read_bytes()
    result = runner.invoke(app, ["report", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "report.json").read_bytes() == before  # deterministic rewrite


def test_report_missing_run_dir_is_usage_error(tmp_path):
    result = runner.invoke(app, ["report", str(tmp_path / "nowhere")])
    assert result.exit_code == 2


def test_stub_replay_dispatch_still_works(tmp_path):
    """`lamarck replay` detects stub runs and keeps the wave-1 behavior;
    --deep on a stub run is a usage error."""
    out = tmp_path / "stub-run"
    run = runner.invoke(app, ["run", "--config", str(WORLD_TOML), "--out", str(out), "--days", "1"])
    assert run.exit_code == 0, run.output
    replay = runner.invoke(app, ["replay", str(out)])
    assert replay.exit_code == 0, replay.output
    assert "replay OK" in replay.output
    deep = runner.invoke(app, ["replay", str(out), "--deep"])
    assert deep.exit_code == 2
