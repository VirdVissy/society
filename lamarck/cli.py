"""lamarck CLI — run and replay Phase-0 simulations.

Two commands (exposed as the ``lamarck`` script via pyproject):

- ``lamarck run --config <toml> [--out DIR] [--days N] [--difftest-interval K]``
  loads and validates the config, runs the sim, and prints a summary table.
  ``--days`` overrides ``world.days`` via a nested ``model_copy``; when it is
  used, the run directory's ``config.toml`` is synthesized from the EFFECTIVE
  config (so the directory always describes the run that actually happened)
  instead of copied verbatim. Default out dir: ``runs/<run_id>``; an existing
  out dir is a polite error, never an overwrite.
- ``lamarck replay RUN_DIR`` prints the model-free replay verdict. Exit code
  0 iff the run verifies; 1 otherwise (per the SPEC §8 replay contract).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lamarck.engine import load_world_config
from lamarck.sim import compute_run_id, run_sim
from lamarck.sim import replay as replay_run

app = typer.Typer(
    help="lamarck — Phase-0 stub-world simulation runner.",
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
    """Run a Phase-0 simulation and write its run directory."""
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


@app.command()
def replay(
    run_dir: Annotated[
        Path,
        typer.Argument(help="Run directory holding events.sqlite3 and config.toml."),
    ],
) -> None:
    """Verify a finished run directory (chain, refold, final state sha)."""
    result = replay_run(run_dir)
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
