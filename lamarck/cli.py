"""lamarck CLI — run, replay, audit and report simulations.

Commands (exposed as the ``lamarck`` script via pyproject):

- ``lamarck run --config <toml> [--out DIR] [--days N] [--difftest-interval K]``
  runs the Phase-0 STUB world (unchanged from wave 1): ``--days`` overrides
  ``world.days`` via a nested ``model_copy`` (the run dir's config.toml is
  then synthesized from the EFFECTIVE config); default out dir
  ``runs/<run_id>``; an existing out dir is a polite error, never an
  overwrite.
- ``lamarck live --config <toml> [--out DIR] [--days N] [--difftest-interval K]
  [--dashboard/--no-dashboard] [--scripted-module MOD:FACTORY]`` runs the
  Phase-1 LIVE world. The backend comes from ``[model]``: ``mlx`` loads the
  real model via ``make_backend``; ``scripted`` REQUIRES ``--scripted-module``
  — a test/CI seam naming a zero-arg factory (``tests.scripted_llm:make``)
  whose return value is a ``(prompt, params) -> str`` callable wrapped in a
  ``ScriptedBackend``. Passing ``--scripted-module`` with ``backend = "mlx"``
  is a usage error (the seam exists for tests/CI only).
- ``lamarck replay RUN_DIR [--deep]`` verifies a finished run. The mode is
  detected from the first event's payload (``mode == "live"``) and
  dispatched to the stub or live verifier; ``--deep`` (live only)
  re-executes the runner against the recorded LLM_CALL stream.
- ``lamarck audit --config <toml>`` builds the configured universe and
  prints the operator audit; exits by ``report.ok``.
- ``lamarck report RUN_DIR`` writes the deterministic ``report.json`` +
  ``report.md`` fold of a finished run.

Exit codes: 0 ok / 1 verification-or-audit failure / 2 usage errors.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lamarck.engine import load_live_config, load_world_config
from lamarck.live import (
    LiveReplayResult,
    LiveRunSummary,
    compute_live_run_id,
    resume_live,
    run_live,
)
from lamarck.live import replay_live as replay_live_run
from lamarck.sim import compute_run_id, run_sim
from lamarck.sim import replay as replay_stub_run

app = typer.Typer(
    help="lamarck — stub (Phase-0) and live (Phase-1) simulation runner.",
    add_completion=False,
    no_args_is_help=True,
)
_out = Console()
_err = Console(stderr=True)


@app.command()
def run(
    config: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False, help="World config TOML file."),
    ],
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Run directory to create (default: runs/<run_id>)."),
    ] = None,
    days: Annotated[
        int | None,
        typer.Option("--days", min=1, help="Override world.days for this run."),
    ] = None,
    difftest_interval: Annotated[
        int,
        typer.Option(
            "--difftest-interval",
            min=0,
            help="Dusk difftest every K days (0 disables per-dusk checks).",
        ),
    ] = 10,
) -> None:
    """Run a Phase-0 stub simulation and write its run directory."""
    try:
        cfg = load_world_config(config)
    except (ValueError, OSError) as err:
        _err.print(f"[red]error:[/red] failed to load {config}: {err}")
        raise typer.Exit(code=2) from err

    config_path: Path | None = config
    if days is not None:
        cfg = cfg.model_copy(update={"world": cfg.world.model_copy(update={"days": days})})
        config_path = None  # synthesize the effective config into the run dir

    out_dir = out if out is not None else Path("runs") / compute_run_id(cfg)
    if out_dir.exists():
        _err.print(
            f"[red]error:[/red] output directory already exists: {out_dir} "
            "(runs are immutable; pick another --out or remove it yourself)"
        )
        raise typer.Exit(code=1)

    summary = run_sim(cfg, out_dir, config_path=config_path, difftest_interval=difftest_interval)

    table = Table(title=f"lamarck run {summary.run_id}", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value", justify="right")
    table.add_row("days", str(summary.days_elapsed))
    table.add_row("events", str(summary.events))
    table.add_row("alive", str(summary.alive_count))
    table.add_row("discoveries", str(summary.discoveries))
    table.add_row("wall ms", str(summary.wall_ms))
    table.add_row("fingerprint", f"{summary.head_seq}/{summary.head_hash[:12]}")
    _out.print(table)
    _out.print(f"run dir: {summary.out_dir}")


def _load_scripted_backend(spec: str) -> object:
    """Resolve ``--scripted-module module.path:factory`` into a backend.

    The named attribute must be a ZERO-ARG factory returning a
    ``(prompt, params) -> str`` callable; the result is wrapped in a
    ``ScriptedBackend``. Exists for tests/CI only (``lamarck live`` has no
    default script by design — mirroring ``make_backend``'s refusal)."""
    from lamarck.serving import ScriptedBackend

    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise ValueError(f"expected MODULE:FACTORY, got {spec!r}")
    module = importlib.import_module(module_name)
    factory = getattr(module, attr)
    return ScriptedBackend(factory())


def _print_live_summary(summary: LiveRunSummary) -> None:
    table = Table(title=f"lamarck live run {summary.run_id}", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value", justify="right")
    table.add_row("days", str(summary.days_elapsed))
    table.add_row("events", str(summary.events))
    table.add_row("alive", str(summary.alive_count))
    table.add_row("llm calls", str(summary.llm_calls))
    table.add_row("tokens in/out", f"{summary.usage_in_total}/{summary.usage_out_total}")
    table.add_row("attempts", str(summary.attempts))
    table.add_row("discoveries", f"{summary.discoveries} ({summary.distinct_discoveries} distinct)")
    table.add_row("degraded", str(summary.degraded))
    table.add_row("retries", f"{summary.retries} ({summary.retry_recovered} recovered)")
    table.add_row("wall ms", str(summary.wall_ms))
    table.add_row("fingerprint", f"{summary.head_seq}/{summary.head_hash[:12]}")
    _out.print(table)
    _out.print(f"run dir: {summary.out_dir}")


@app.command()
def live(
    config: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False, help="Live config TOML file."),
    ] = Path("configs/valley.toml"),
    out: Annotated[
        Path | None,
        typer.Option("--out", help="Run directory to create (default: runs/<run_id>)."),
    ] = None,
    days: Annotated[
        int | None,
        typer.Option("--days", min=1, help="Override world.days for this run."),
    ] = None,
    difftest_interval: Annotated[
        int,
        typer.Option(
            "--difftest-interval",
            min=0,
            help="Dusk difftest every K days (0 disables per-dusk checks).",
        ),
    ] = 10,
    dashboard: Annotated[
        bool,
        typer.Option("--dashboard/--no-dashboard", help="Per-day rich live table."),
    ] = False,
    scripted_module: Annotated[
        str | None,
        typer.Option(
            "--scripted-module",
            help=(
                "TEST/CI SEAM for backend='scripted': MODULE:FACTORY naming a zero-arg "
                "factory returning a (prompt, params) -> str callable "
                "(e.g. tests.scripted_llm:make). Errors when backend='mlx'."
            ),
        ),
    ] = None,
) -> None:
    """Run a Phase-1 live simulation and write its run directory."""
    try:
        cfg = load_live_config(config)
    except (ValueError, OSError) as err:
        _err.print(f"[red]error:[/red] failed to load {config}: {err}")
        raise typer.Exit(code=2) from err

    config_path: Path | None = config
    if days is not None:
        cfg = cfg.model_copy(update={"world": cfg.world.model_copy(update={"days": days})})
        config_path = None  # synthesize the effective config into the run dir

    backend = None
    if cfg.model.backend == "scripted":
        if scripted_module is None:
            _err.print(
                "[red]error:[/red] backend 'scripted' has no default script: pass "
                "--scripted-module MODULE:FACTORY (test/CI seam, e.g. tests.scripted_llm:make)"
            )
            raise typer.Exit(code=2)
        try:
            backend = _load_scripted_backend(scripted_module)
        except (ValueError, ImportError, AttributeError, TypeError) as err:
            _err.print(f"[red]error:[/red] failed to load --scripted-module: {err}")
            raise typer.Exit(code=2) from err
    elif scripted_module is not None:
        _err.print(
            "[red]error:[/red] --scripted-module is a test/CI seam for backend='scripted'; "
            f"this config uses backend={cfg.model.backend!r}"
        )
        raise typer.Exit(code=2)

    out_dir = out if out is not None else Path("runs") / compute_live_run_id(cfg)
    if out_dir.exists():
        _err.print(
            f"[red]error:[/red] output directory already exists: {out_dir} "
            "(runs are immutable; pick another --out or remove it yourself)"
        )
        raise typer.Exit(code=1)

    summary = run_live(
        cfg,
        out_dir,
        config_path=config_path,
        backend=backend,  # type: ignore[arg-type]  # ScriptedBackend | None; mlx when None
        difftest_interval=difftest_interval,
        dashboard=dashboard,
    )
    _print_live_summary(summary)


@app.command()
def resume(
    run_dir: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, help="Interrupted live run directory."),
    ],
    difftest_interval: Annotated[
        int,
        typer.Option(
            "--difftest-interval",
            min=0,
            help="Dusk difftest every K days (0 disables per-dusk checks).",
        ),
    ] = 10,
    dashboard: Annotated[
        bool,
        typer.Option("--dashboard/--no-dashboard", help="Per-day rich live table."),
    ] = False,
    scripted_module: Annotated[
        str | None,
        typer.Option(
            "--scripted-module",
            help=(
                "TEST/CI SEAM for backend='scripted': MODULE:FACTORY naming a zero-arg "
                "factory returning a (prompt, params) -> str callable."
            ),
        ),
    ] = None,
) -> None:
    """Continue an interrupted live run from its last completed day.

    The run's own config.toml is the configuration; the log is verified,
    the engine state is rebuilt from it, and days continue until
    world.days or extinction. Refuses finished, foreign, or mixed-template
    runs — see resume_live's docstring for the exact checks.
    """
    try:
        cfg = load_live_config(run_dir / "config.toml")
    except (ValueError, OSError) as err:
        _err.print(f"[red]error:[/red] failed to load {run_dir / 'config.toml'}: {err}")
        raise typer.Exit(code=2) from err

    backend = None
    if cfg.model.backend == "scripted":
        if scripted_module is None:
            _err.print(
                "[red]error:[/red] backend 'scripted' has no default script: pass "
                "--scripted-module MODULE:FACTORY (test/CI seam, e.g. tests.scripted_llm:make)"
            )
            raise typer.Exit(code=2)
        try:
            backend = _load_scripted_backend(scripted_module)
        except (ValueError, ImportError, AttributeError, TypeError) as err:
            _err.print(f"[red]error:[/red] failed to load --scripted-module: {err}")
            raise typer.Exit(code=2) from err
    elif scripted_module is not None:
        _err.print(
            "[red]error:[/red] --scripted-module is a test/CI seam for backend='scripted'; "
            f"this config uses backend={cfg.model.backend!r}"
        )
        raise typer.Exit(code=2)

    try:
        summary = resume_live(
            run_dir,
            backend=backend,  # type: ignore[arg-type]  # ScriptedBackend | None
            difftest_interval=difftest_interval,
            dashboard=dashboard,
        )
    except ValueError as err:
        _err.print(f"[red]error:[/red] cannot resume: {err}")
        raise typer.Exit(code=1) from err
    _print_live_summary(summary)


def _detect_mode(run_dir: Path) -> str:
    """ "live" when the first event is a live-mode run_started, else "stub".

    Best-effort raw read (no chain assertions): undetectable logs fall to
    the stub verifier, whose failure messages are the politest."""
    db_path = run_dir / "events.sqlite3"
    if not db_path.is_file():
        return "stub"
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT payload FROM events WHERE seq = 0").fetchone()
        finally:
            conn.close()
        if row is None:
            return "stub"
        payload = json.loads(row[0])
    except (sqlite3.Error, ValueError):
        return "stub"
    return "live" if isinstance(payload, dict) and payload.get("mode") == "live" else "stub"


def _print_live_replay(result: LiveReplayResult) -> None:
    if result.ok:
        deep_note = (
            f", deep re-execution matched head {result.replayed_head_seq}"
            if result.mode == "deep"
            else ""
        )
        _out.print(
            f"[green]replay OK[/green] ({result.mode}) — {result.events} events, "
            f"head {result.head_seq}/{result.head_hash[:12]}, "
            f"days_elapsed={result.days_elapsed}, "
            f"{result.attempts_checked} attempts re-verified{deep_note}"
        )
        return
    _err.print(f"[red]replay FAILED[/red] ({result.mode}) — {result.run_dir}")
    for mismatch in result.mismatches:
        _err.print(f"  - {mismatch}")
    raise typer.Exit(code=1)


@app.command()
def replay(
    run_dir: Annotated[
        Path,
        typer.Argument(help="Run directory holding events.sqlite3 and config.toml."),
    ],
    deep: Annotated[
        bool,
        typer.Option(
            "--deep",
            help="Live runs only: re-execute the runner against the recorded LLM_CALL stream.",
        ),
    ] = False,
) -> None:
    """Verify a finished run directory (stub or live; detected from the log)."""
    mode = _detect_mode(run_dir)
    if mode == "live":
        _print_live_replay(replay_live_run(run_dir, deep=deep))
        return
    if deep:
        _err.print("[red]error:[/red] --deep applies to live runs only (this run is stub-mode)")
        raise typer.Exit(code=2)
    result = replay_stub_run(run_dir)
    if result.ok:
        _out.print(
            f"[green]replay OK[/green] — {result.events} events, "
            f"head {result.head_seq}/{result.head_hash[:12]}, "
            f"days_elapsed={result.days_elapsed}, final_state_sha verified"
        )
        return
    _err.print(f"[red]replay FAILED[/red] — {result.run_dir}")
    for mismatch in result.mismatches:
        _err.print(f"  - {mismatch}")
    raise typer.Exit(code=1)


@app.command()
def audit(
    config: Annotated[
        Path,
        typer.Option("--config", exists=True, dir_okay=False, help="Live config TOML file."),
    ] = Path("configs/valley.toml"),
) -> None:
    """Audit the configured universe (structure + budgets) before a run."""
    from lamarck.universes.audit import main as audit_main

    raise typer.Exit(code=audit_main([str(config)]))


@app.command()
def report(
    run_dir: Annotated[
        Path,
        typer.Argument(help="Run directory holding events.sqlite3."),
    ],
) -> None:
    """Write the deterministic report.json + report.md for a finished run."""
    if not (run_dir / "events.sqlite3").is_file():
        _err.print(f"[red]error:[/red] no events.sqlite3 in {run_dir}")
        raise typer.Exit(code=2)
    from lamarck.analysis import write_report

    try:
        json_path = write_report(run_dir)
    except Exception as err:
        _err.print(f"[red]error:[/red] report fold failed: {err}")
        raise typer.Exit(code=1) from err
    _out.print(f"wrote {json_path} and {json_path.with_suffix('.md')}")
