"""lamarck.serving — model backends behind ``contracts.ModelBackendP``.

Public API:

- :func:`sanitize_model_text` — lone surrogates -> U+FFFD, NULs stripped
  (applied by the runner to raw backend output).
- :class:`ScriptedBackend` — deterministic scripted generator (CI workhorse).
- :class:`MlxBackend` — real local generation via mlx-lm (lazy import).
- :func:`make_backend` — construct from a ``ModelSection``.
"""

from lamarck.serving.backends import (
    MlxBackend,
    ScriptedBackend,
    make_backend,
    sanitize_model_text,
)

__all__ = ["MlxBackend", "ScriptedBackend", "make_backend", "sanitize_model_text"]
