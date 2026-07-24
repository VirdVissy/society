"""lamarck.mind — personas, prompt rendering, and action parsing (Phase 1).

Public API:

- :func:`load_personas` / :func:`founder_ids` — immutable birth identities
  and deterministic spawn order.
- :data:`TEMPLATE_VERSION`, :func:`render_system`, :func:`render_user`,
  :func:`render_reflection_user`, :func:`build_prompt`, :func:`prompt_sha`,
  :func:`enforce_budget` — the versioned, deterministic prompt boundary.
- :class:`ParseFailure`, :func:`parse_action`, :func:`retry_message` — the
  action protocol's parser and its corrective feedback.
"""

from lamarck.mind.parser import ParseFailure, parse_action, retry_message
from lamarck.mind.personas import founder_ids, load_personas
from lamarck.mind.prompt import (
    TEMPLATE_VERSION,
    build_prompt,
    enforce_budget,
    prompt_sha,
    render_reflection_user,
    render_system,
    render_user,
)

__all__ = [
    "TEMPLATE_VERSION",
    "ParseFailure",
    "build_prompt",
    "enforce_budget",
    "founder_ids",
    "load_personas",
    "parse_action",
    "prompt_sha",
    "render_reflection_user",
    "render_system",
    "render_user",
    "retry_message",
]
