"""CLI acceptance: `lamarck run` and `lamarck replay` via typer's CliRunner.

Exit-code contract (SPEC §8): replay exits 0 iff the run verifies, 1
otherwise. Runs are invoked with --days 3 to stay fast; the days override
must be honored end-to-end (report.json and the synthesized run-dir
config.toml both say 3, regardless of the source file's 30).
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
CONFIG_PATH = REPO_ROOT / "configs" / "world.toml"

runner = CliRunner()


def _tamper_one_payload(run_dir: Path) -> None:
    """Flip one committed payload value (canonical text, broken hash)."""
    conn = sqlite3.connect(run_dir / "events.sqlite3")
    try:
        seq, payload = conn.execute(
            "SELECT seq, payload FROM events WHERE kind = 'day_started' ORDER BY seq LIMIT 1"
        ).fetchone()
        tampered = canonical_bytes({"day": 999}).decode("utf-8")
        assert tampered != payload
        conn.execute("UPDATE events SET payload = ? WHERE seq = ?", (tampered, seq))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def cli_run_dir(tmp_path_factory):
    """One 3-day CLI run shared by the module's tests (runs are immutable)."""
    out = tmp_path_factory.mktemp("cli") / "run"
    result = runner.invoke(
        app, ["run", "--config", str(CONFIG_PATH), "--out", str(out), "--days", "3"]
    )
    assert result.exit_code == 0, result.output
    return out, result


def test_run_exits_zero_with_summary(cli_run_dir):
    out, result = cli_run_dir
    assert "days" in result.output
    assert "events" in result.output
    assert "fingerprint" in result.output
    assert (out / "events.sqlite3").is_file()
    assert (out / "config.toml").is_file()
    assert (out / "report.json").is_file()


def test_days_override_respected(cli_run_dir):
    out, _ = cli_run_dir
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["days_elapsed"] == 3
    # With --days the run dir's config.toml is synthesized from the
    # EFFECTIVE config, so it must carry the override, not the source's 30.
    assert "days = 3" in (out / "config.toml").read_text(encoding="utf-8")


def test_replay_exits_zero_on_good_run(cli_run_dir):
    out, _ = cli_run_dir
    result = runner.invoke(app, ["replay", str(out)])
    assert result.exit_code == 0, result.output
    assert "replay OK" in result.output


def test_replay_exits_one_on_tampered_copy(cli_run_dir, tmp_path):
    out, _ = cli_run_dir
    tampered = tmp_path / "tampered"
    shutil.copytree(out, tampered)
    _tamper_one_payload(tampered)
    result = runner.invoke(app, ["replay", str(tampered)])
    assert result.exit_code == 1


def test_run_refuses_existing_out_dir(tmp_path):
    existing = tmp_path / "occupied"
    existing.mkdir()
    result = runner.invoke(
        app, ["run", "--config", str(CONFIG_PATH), "--out", str(existing), "--days", "1"]
    )
    assert result.exit_code == 1
