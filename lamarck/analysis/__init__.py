"""lamarck.analysis — post-run analysis over finished run directories.

Public API:

- :func:`write_report` — deterministic ``report.json`` + ``report.md`` from
  a pure fold of a run's event log (byte-identical across re-writes).
"""

from lamarck.analysis.report import write_report

__all__ = ["write_report"]
