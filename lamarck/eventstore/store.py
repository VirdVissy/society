"""Append-only, hash-chained event log on SQLite — the run's ground truth.

Layout (contracts: "HASH CHAIN"):

    events(seq INTEGER PRIMARY KEY, day, tick, kind, actor, payload TEXT,
           qi_delta, stones_delta, hash TEXT, wall_ts REAL)

- ``seq`` is dense from 0 (LMK_ASSERT-checked on open and in verify_chain).
- ``payload`` stores the canonical JSON text of the event payload.
- ``wall_ts`` is convenience telemetry only and is NEVER hashed — replay is
  time-independent.
- ``hash_i = sha256_hex(utf8(prev_hash_hex) || canonical_bytes(envelope_i))``
  where the envelope has exactly the keys ``{seq, day, tick, kind, actor,
  payload, qi_delta, stones_delta}``, ``kind`` is the EventKind str value,
  and ``prev`` of seq 0 is GENESIS_HASH. The head ``(last_seq, last_hash)``
  is the run fingerprint.

Discipline:

- One long-lived connection per store; WAL journal, ``synchronous=NORMAL``.
  Single writer: ``batch()`` is not reentrant (LMK_ASSERT), and the
  connection rejects cross-thread use (sqlite3 default).
- Appends outside ``batch()`` autocommit one by one; inside ``batch()`` they
  commit atomically on exit, and roll back — including the in-memory head —
  if the body raises. A failed append never advances the head.
- Committed content is normalized: ``actor`` and payload strings are NFC and
  the payload column is canonical text, so ``append`` returns exactly the
  record ``scan`` will later yield for that seq.
- ``verify_chain`` is the replay-integrity primitive: it recomputes every
  hash from genesis and asserts that the stored payload text is
  byte-identical to the canonical re-encoding of its parsed content.
- The chain detects any in-place mutation, insertion, or mid-log deletion.
  Truncation of the *tail* is detectable only against an externally held
  head; callers needing truncation evidence must persist ``head()``.
- ``qi_delta``/``stones_delta`` live in 64-bit INTEGER columns (SQLite
  limit); ints inside payloads are arbitrary-precision (JSON text).
"""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any

from lamarck.asserts import LMK_ASSERT, LamarckAssertionError
from lamarck.contracts import GENESIS_HASH, EventDraft, EventKind, EventRecord, EventStoreP
from lamarck.eventstore.canonical import CanonicalError, canonical_bytes, sha256_hex

__all__ = ["EventStore"]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY,
  day INTEGER NOT NULL,
  tick INTEGER NOT NULL,
  kind TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload TEXT NOT NULL,
  qi_delta INTEGER NOT NULL,
  stones_delta INTEGER NOT NULL,
  hash TEXT NOT NULL,
  wall_ts REAL
)
"""

_INSERT_SQL = (
    "INSERT INTO events(seq, day, tick, kind, actor, payload, qi_delta, stones_delta,"
    " hash, wall_ts) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

_SCAN_SQL = (
    "SELECT seq, day, tick, kind, actor, payload, qi_delta, stones_delta, hash"
    " FROM events WHERE seq >= ? ORDER BY seq"
)


def _record_from_row(row: tuple[Any, ...]) -> EventRecord:
    return EventRecord(
        seq=row[0],
        day=row[1],
        tick=row[2],
        kind=EventKind(row[3]),
        actor=row[4],
        payload=json.loads(row[5]),
        qi_delta=row[6],
        stones_delta=row[7],
        hash=row[8],
    )


class EventStore:
    """contracts.EventStoreP implementation (structural). See module docstring.

    File-backed only: WAL requires a real file, so ``:memory:`` is
    unsupported (the constructor asserts the journal mode took effect).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        conn = sqlite3.connect(self._path, isolation_level=None)  # autocommit; batch() BEGINs
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
        self._in_batch = False
        row = conn.execute("SELECT COUNT(*), MIN(seq), MAX(seq) FROM events").fetchone()
        count, min_seq, max_seq = int(row[0]), row[1], row[2]
        if count == 0:
            self._last_seq, self._last_hash = -1, GENESIS_HASH
        else:
            LMK_ASSERT(
                min_seq == 0 and max_seq == count - 1,
                "event log is not contiguous from seq 0",
                path=str(self._path),
                count=count,
                min_seq=min_seq,
                max_seq=max_seq,
            )
            last = conn.execute("SELECT hash FROM events WHERE seq = ?", (max_seq,)).fetchone()
            self._last_seq, self._last_hash = int(max_seq), str(last[0])

    # ------------------------------------------------------------- lifecycle

    def close(self) -> None:
        """Close the store (idempotent). Closing inside an open batch is a bug."""
        if self._conn is None:
            return
        LMK_ASSERT(not self._in_batch, "close() inside an open batch()", path=str(self._path))
        self._conn.close()
        self._conn = None

    def __enter__(self) -> EventStore:
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
            raise LamarckAssertionError(f"event store is closed | path={str(self._path)!r}")
        return conn

    # --------------------------------------------------------------- writing

    def append(self, draft: EventDraft) -> EventRecord:
        """Commit *draft* as the next event and return the committed record.

        Raises CanonicalError (and writes nothing) if the payload is outside
        the canonical-JSON domain — e.g. contains a float.
        """
        conn = self._require_open()
        seq = self._last_seq + 1
        actor = unicodedata.normalize("NFC", draft.actor)
        kind = draft.kind.value
        payload_text = canonical_bytes(draft.payload).decode("utf-8")
        envelope = {
            "seq": seq,
            "day": draft.day,
            "tick": draft.tick,
            "kind": kind,
            "actor": actor,
            "payload": draft.payload,
            "qi_delta": draft.qi_delta,
            "stones_delta": draft.stones_delta,
        }
        event_hash = sha256_hex(self._last_hash.encode("utf-8") + canonical_bytes(envelope))
        conn.execute(
            _INSERT_SQL,
            (
                seq,
                draft.day,
                draft.tick,
                kind,
                actor,
                payload_text,
                draft.qi_delta,
                draft.stones_delta,
                event_hash,
                time.time(),
            ),
        )
        self._last_seq = seq
        self._last_hash = event_hash
        return EventRecord(
            seq=seq,
            day=draft.day,
            tick=draft.tick,
            kind=draft.kind,
            actor=actor,
            payload=json.loads(payload_text),
            qi_delta=draft.qi_delta,
            stones_delta=draft.stones_delta,
            hash=event_hash,
        )

    @contextmanager
    def batch(self) -> Iterator[None]:
        """One transaction around many appends (perf; semantics unchanged).

        Commits on clean exit; on exception rolls back both the database and
        the in-memory head, then re-raises. Not reentrant (single-writer
        discipline).
        """
        conn = self._require_open()
        LMK_ASSERT(not self._in_batch, "batch() is not reentrant", path=str(self._path))
        saved_head = (self._last_seq, self._last_hash)
        conn.execute("BEGIN")
        self._in_batch = True
        try:
            yield
        except BaseException:
            self._in_batch = False
            conn.execute("ROLLBACK")
            self._last_seq, self._last_hash = saved_head
            raise
        self._in_batch = False
        try:
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            self._last_seq, self._last_hash = saved_head
            raise

    # --------------------------------------------------------------- reading

    def head(self) -> tuple[int, str]:
        """(last_seq, last_hash); (-1, GENESIS_HASH) when empty."""
        self._require_open()
        return (self._last_seq, self._last_hash)

    def scan(self, from_seq: int = 0) -> Iterator[EventRecord]:
        """Stream records with ``seq >= from_seq`` in seq order.

        Same-connection reads: inside an open batch() a scan also sees that
        batch's uncommitted appends.
        """
        conn = self._require_open()
        rows = conn.execute(_SCAN_SQL, (from_seq,))
        return (_record_from_row(row) for row in rows)

    def verify_chain(self) -> tuple[int, str]:
        """Recompute every hash from genesis; return the verified head.

        LMK_ASSERTs, per event: seq is dense; the payload column parses and
        re-encodes byte-identically to canonical JSON; the stored hash equals
        the recomputed one. Finally asserts the verified head equals the
        in-memory head. This is the replay-integrity primitive.
        """
        conn = self._require_open()
        prev = GENESIS_HASH
        next_seq = 0
        for row in conn.execute(_SCAN_SQL, (0,)):
            seq, day, tick, kind, actor, payload_text, qi_delta, stones_delta, stored_hash = row
            LMK_ASSERT(seq == next_seq, "sequence gap in event log", expected=next_seq, found=seq)
            try:
                payload = json.loads(payload_text)
                payload_bytes = canonical_bytes(payload)
            except (json.JSONDecodeError, CanonicalError) as err:
                raise LamarckAssertionError(
                    f"stored payload is not canonical JSON | seq={seq}, cause={err}"
                ) from err
            LMK_ASSERT(isinstance(payload, dict), "payload is not a JSON object", seq=seq)
            LMK_ASSERT(
                payload_bytes == str(payload_text).encode("utf-8"),
                "payload text is not canonical",
                seq=seq,
            )
            envelope = {
                "seq": seq,
                "day": day,
                "tick": tick,
                "kind": kind,
                "actor": actor,
                "payload": payload,
                "qi_delta": qi_delta,
                "stones_delta": stones_delta,
            }
            try:
                envelope_bytes = canonical_bytes(envelope)
            except CanonicalError as err:  # e.g. a delta column tampered to REAL
                raise LamarckAssertionError(
                    f"envelope is not canonical | seq={seq}, cause={err}"
                ) from err
            recomputed = sha256_hex(prev.encode("utf-8") + envelope_bytes)
            LMK_ASSERT(
                recomputed == stored_hash,
                "hash chain broken",
                seq=seq,
                stored=stored_hash,
                recomputed=recomputed,
            )
            prev = str(stored_hash)
            next_seq += 1
        verified = (next_seq - 1, prev)
        LMK_ASSERT(
            verified == self.head(),
            "verified head disagrees with in-memory head",
            verified=verified,
            memory=self.head(),
        )
        return verified


def _proves_protocol(store: EventStore) -> EventStoreP:
    """Compile-time (mypy) proof that EventStore structurally satisfies EventStoreP."""
    return store
