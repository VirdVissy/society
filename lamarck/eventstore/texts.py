"""Unhashed side storage for full LLM prompt texts (contracts: "Determinism").

Every ``LLM_CALL`` event carries only ``prompt_sha256`` in its hashed
payload; the full prompt text lands here, keyed by the event's ``seq``:

    llm_texts(seq INTEGER PRIMARY KEY, prompt TEXT NOT NULL)

The table lives in the SAME SQLite file as the event log (one artifact per
run) but this class opens its OWN connection and never touches the
``events`` table. Nothing in ``llm_texts`` is ever hashed, folded, or read
by replay-verify: dropping the whole table cannot change any fingerprint,
any ledger balance, or the result of ``verify_chain`` (a test proves the
events table is untouched by interleaved use). Deep replay reads prompts
back only to re-assert their sha against the hashed payload.

Write discipline: ``put`` refuses to overwrite a seq with DIFFERENT content
(LMK_ASSERT — one seq, one prompt, forever) and is idempotent for identical
content (crash-rerun friendly). Prompts are stored verbatim: the string is
already the canonical-JSON envelope produced by ``lamarck.mind.prompt``, so
re-normalizing here would be a second serializer by the back door.

Concurrency note: WAL allows this connection to coexist with the event
store's, but SQLite still permits one writer at a time. ``put`` must not be
called while another connection holds an open write transaction on the same
file — i.e. the runner writes prompt texts OUTSIDE ``EventStore.batch()``
windows (violations surface immediately as ``sqlite3.OperationalError:
database is locked``, never as silent corruption).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import TracebackType

from lamarck.asserts import LMK_ASSERT, LamarckAssertionError

__all__ = ["TextsStore"]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS llm_texts(
  seq INTEGER PRIMARY KEY,
  prompt TEXT NOT NULL
)
"""

_SELECT_SQL = "SELECT prompt FROM llm_texts WHERE seq = ?"
_INSERT_SQL = "INSERT INTO llm_texts(seq, prompt) VALUES(?, ?)"


class TextsStore:
    """Side table of full prompt texts; see module docstring.

    File-backed only, like the event store it shares a file with: WAL is
    asserted so ``:memory:`` misuse fails loudly (WAL is a property of the
    database file — setting it here and in EventStore is idempotent).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        conn = sqlite3.connect(self._path, isolation_level=None)  # autocommit
        self._conn: sqlite3.Connection | None = conn
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        LMK_ASSERT(
            str(mode).lower() == "wal",
            "WAL journal mode did not take effect",
            path=str(self._path),
            mode=mode,
        )
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(_SCHEMA_SQL)

    # ------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Close the store (idempotent)."""
        if self._conn is None:
            return
        self._conn.close()
        self._conn = None

    def __enter__(self) -> TextsStore:
        self._require_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _require_open(self) -> sqlite3.Connection:
        conn = self._conn
        if conn is None:  # raised directly (not LMK_ASSERT) so mypy narrows the Optional
            raise LamarckAssertionError(f"texts store is closed | path={str(self._path)!r}")
        return conn

    # ------------------------------------------------------------------- api

    def put(self, seq: int, prompt: str) -> None:
        """Store *prompt* for event *seq*.

        Idempotent for identical content; LMK_ASSERTs against overwriting a
        seq with different content (one seq, one prompt, forever).
        """
        conn = self._require_open()
        LMK_ASSERT(seq >= 0, "llm_texts seq must be >= 0", seq=seq)
        row = conn.execute(_SELECT_SQL, (seq,)).fetchone()
        if row is not None:
            LMK_ASSERT(
                str(row[0]) == prompt,
                "llm_texts overwrite with different content",
                seq=seq,
                path=str(self._path),
            )
            return  # idempotent re-put of identical content
        conn.execute(_INSERT_SQL, (seq, prompt))

    def get(self, seq: int) -> str | None:
        """Return the stored prompt for *seq*, or None when absent."""
        conn = self._require_open()
        row = conn.execute(_SELECT_SQL, (seq,)).fetchone()
        return None if row is None else str(row[0])
