"""Always-on assertions (house convention: assertions are never compiled out).

Use LMK_ASSERT for every internal invariant. It raises regardless of python -O,
and carries structured context for postmortems.
"""

from typing import Any


class LamarckAssertionError(AssertionError):
    """An engine invariant was violated. Always a bug, never user error."""


def LMK_ASSERT(cond: bool, msg: str, **ctx: Any) -> None:  # noqa: N802 (house name)
    if not cond:
        detail = "" if not ctx else " | " + ", ".join(f"{k}={v!r}" for k, v in sorted(ctx.items()))
        raise LamarckAssertionError(f"{msg}{detail}")
