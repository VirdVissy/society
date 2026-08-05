"""resume_live: crash mid-day, rebuild from the log, continue, verify.

The load-bearing assertion is DEEP replay of the resumed run: it re-executes
days 0..N in ONE process against the recorded LLM_CALL stream, so the
resumed run's scheduler orders and llm-seed draws must equal what a
never-crashed process would have produced — any RNG-reconstruction error
surfaces as a prompt-sha or chain-head mismatch. (The resumed run's CONTENT
legitimately differs from a hypothetical uninterrupted run — the scripted
policy restarts cold — so no test compares those two heads.)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lamarck.contracts import EventKind, GenParams, GenResult, ModelBackendP
from lamarck.engine import load_live_config
from lamarck.eventstore import EventStore
from lamarck.live import replay_live, resume_live, run_live
from lamarck.serving import ScriptedBackend
from tests.scripted_llm import make as make_scripted

REPO_ROOT = Path(__file__).resolve().parents[1]
VALLEY_TOML = REPO_ROOT / "configs" / "valley.toml"


class _BombBackend:
    """ScriptedBackend that detonates after ``fuse`` generate calls."""

    def __init__(self, fuse: int) -> None:
        self._inner = ScriptedBackend(make_scripted())
        self._fuse = fuse
        self.calls = 0

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        if self.calls >= self._fuse:
            raise RuntimeError("bomb: simulated 529 window")
        self.calls += 1
        return self._inner.generate(prompt, params)


_proof: tuple[ModelBackendP, ...] = (_BombBackend(0),)  # structural-typing proof


def _cfg():
    cfg = load_live_config(VALLEY_TOML)
    return cfg.model_copy(
        update={
            "world": cfg.world.model_copy(update={"days": 4}),
            "model": cfg.model.model_copy(update={"backend": "scripted"}),
        }
    )


def _events(run_dir: Path):
    with EventStore(run_dir / "events.sqlite3") as store:
        return list(store.scan())


@pytest.fixture(scope="module")
def resume_dirs(tmp_path_factory) -> tuple[Path, Path]:
    """A 4-day run bombed mid-flight (fuse 210 = inside day 2; the scripted
    schedule burns 96/80/72/72 calls per day), snapshotted, then resumed to
    completion. Yields (finished_run_dir, partial_snapshot_dir)."""
    run_dir = tmp_path_factory.mktemp("resume") / "run"
    cfg = _cfg()
    with pytest.raises(RuntimeError, match="bomb"):
        run_live(cfg, run_dir, config_path=None, backend=_BombBackend(210), difftest_interval=1)

    # The partial log ends at a clean completed-day boundary: the bombed
    # day's batch rolled back whole.
    partial = _events(run_dir)
    assert not [ev for ev in partial if ev.kind is EventKind.RUN_FINISHED]
    last = partial[-1]
    assert last.kind is EventKind.PHASE_STARTED and last.payload["phase"] == "night"
    max_day = max(ev.day for ev in partial)
    assert 1 <= max_day < 3  # died before the final day; resume has work to do

    partial_dir = tmp_path_factory.mktemp("resume-partial") / "run"
    shutil.copytree(run_dir, partial_dir)

    summary = resume_live(run_dir, backend=ScriptedBackend(make_scripted()), difftest_interval=1)
    assert summary.days_elapsed == 4
    assert summary.alive_count == 8
    return run_dir, partial_dir


@pytest.fixture(scope="module")
def interrupted_run(resume_dirs: tuple[Path, Path]) -> Path:
    return resume_dirs[0]


@pytest.fixture(scope="module")
def partial_run(resume_dirs: tuple[Path, Path]) -> Path:
    return resume_dirs[1]


def test_resumed_run_is_whole(interrupted_run: Path) -> None:
    events = _events(interrupted_run)
    finished = [ev for ev in events if ev.kind is EventKind.RUN_FINISHED]
    assert len(finished) == 1 and finished[0].seq == events[-1].seq
    assert finished[0].payload["days_elapsed"] == 4
    day_starts = sorted(ev.payload["day"] for ev in events if ev.kind is EventKind.DAY_STARTED)
    assert day_starts == [0, 1, 2, 3]  # every day exactly once, no repeats


def test_resumed_run_shallow_replay_ok(interrupted_run: Path) -> None:
    result = replay_live(interrupted_run)
    assert result.ok, result.mismatches[:3]


def test_resumed_run_deep_replay_reproduces_head(interrupted_run: Path) -> None:
    """THE reconstruction prover: one-process re-execution must land on the
    resumed run's exact chain head."""
    result = replay_live(interrupted_run, deep=True)
    assert result.ok, result.mismatches[:3]


def test_resumed_summary_counters_match_refold(interrupted_run: Path) -> None:
    events = _events(interrupted_run)
    attempts = [ev for ev in events if ev.kind is EventKind.TASK_ATTEMPT]
    claims = [c for ev in attempts for c in ev.payload["claims"]]
    resumed_again = pytest.raises(ValueError, resume_live, interrupted_run)
    assert "already finished" in str(resumed_again.value)
    assert len(attempts) >= 100  # the society stayed busy across the seam
    assert {c["task_id"] for c in claims}  # and kept discovering


def test_resume_refuses_config_drift(tmp_path: Path, partial_run: Path) -> None:
    clone = tmp_path / "drift"
    shutil.copytree(partial_run, clone)
    config = clone / "config.toml"
    config.write_text(config.read_text().replace("bounties = [10,", "bounties = [11,"))
    with pytest.raises(ValueError, match="config changed"):
        resume_live(clone, backend=ScriptedBackend(make_scripted()))


def test_resume_refuses_template_mismatch(
    tmp_path: Path, partial_run: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "tmpl"
    shutil.copytree(partial_run, clone)
    monkeypatch.setattr("lamarck.live.TEMPLATE_VERSION", "p9.9")
    with pytest.raises(ValueError, match="mix prompt templates"):
        resume_live(clone, backend=ScriptedBackend(make_scripted()))


def test_resume_refuses_non_run_dir(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a run directory"):
        resume_live(tmp_path / "nowhere")


def test_cli_resume_wiring(tmp_path: Path) -> None:
    """`lamarck resume` continues a bombed scripted run (exit 0) and refuses
    a finished one (exit 1)."""
    from typer.testing import CliRunner

    from lamarck.cli import app

    run_dir = tmp_path / "run"
    with pytest.raises(RuntimeError, match="bomb"):
        run_live(_cfg(), run_dir, config_path=None, backend=_BombBackend(210), difftest_interval=0)
    runner = CliRunner()
    ok = runner.invoke(
        app,
        ["resume", str(run_dir), "--scripted-module", "tests.scripted_llm:make"],
    )
    assert ok.exit_code == 0, ok.output
    again = runner.invoke(
        app,
        ["resume", str(run_dir), "--scripted-module", "tests.scripted_llm:make"],
    )
    assert again.exit_code == 1
    assert "already finished" in again.output
