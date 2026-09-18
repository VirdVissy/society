"""lamarck.analysis — post-run analysis over finished run directories.

Every function here is a pure fold of a run's event log (opened read-only;
the run directory is never modified except for the tool's own output file)
and writes deterministic, byte-identical JSON: ints, strings, bools only.

Public API:

- :func:`write_report` — ``report.json`` + ``report.md`` (Phase 1).
- :func:`write_exposure` — recipe exposure, uptake, acquisition classes and
  misinformation statistics (Phase-2 Stage 0, the attribution prototype).
- :func:`write_lifespan` — death/succession schedule simulation from
  measured spend (Phase-2 Stage 0, canary bars).
- :func:`write_retread` — pair-level own-journal retread and truncation
  statistics (Phase-2 Stage 0, locked metric baselines).
"""

from lamarck.analysis.exposure import write_exposure
from lamarck.analysis.lifespan import write_lifespan
from lamarck.analysis.report import write_report
from lamarck.analysis.retread import write_retread

__all__ = ["write_exposure", "write_lifespan", "write_report", "write_retread"]
