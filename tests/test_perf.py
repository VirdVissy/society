"""Performance gate: a 1000-day, 8-agent run must finish in under 5 seconds.

The gate times ``run_sim`` alone (which internally includes the end-of-run
``verify_chain`` and the one unconditional difftest — those are part of every
run by contract). Per-dusk difftests are disabled (``difftest_interval=0``)
because each one rescans the whole log — O(n²) across a long run — and the
gate measures the runner, not repeated auditing. ``qi_max`` is raised to
10_000_000 so all founders survive the full span (the worst case: every slot
stays populated).
"""

import time
from pathlib import Path

from lamarck.engine import load_world_config
from lamarck.sim import run_sim

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "world.toml"

PERF_DAYS = 1000
PERF_QI_MAX = 10_000_000
PERF_BUDGET_S = 5.0


def test_thousand_day_run_under_budget(tmp_path):
    cfg = load_world_config(CONFIG_PATH)
    cfg = cfg.model_copy(update={"world": cfg.world.model_copy(update={"days": PERF_DAYS})})
    cfg = cfg.model_copy(update={"qi": cfg.qi.model_copy(update={"qi_max": PERF_QI_MAX})})

    t0 = time.perf_counter()
    summary = run_sim(cfg, tmp_path / "perf", difftest_interval=0)
    wall = time.perf_counter() - t0

    # The run must actually be the full-size workload before timing counts.
    assert summary.days_elapsed == PERF_DAYS
    assert summary.alive_count == cfg.population.founders

    print(
        f"\nperf: {wall:.3f}s for {summary.events} events "
        f"({summary.events / wall:,.0f} events/s; budget {PERF_BUDGET_S}s)"
    )
    assert wall < PERF_BUDGET_S, f"1000-day run took {wall:.3f}s (budget {PERF_BUDGET_S}s)"
