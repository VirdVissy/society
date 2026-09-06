"""Tests for lamarck.eventstore.store — the hash-chained append-only log.

The three-event chain fixture is verified three independent ways: the
envelope JSON is hand-written here as string literals, the chain is
recomputed with hashlib alone, and both must agree with the pinned hash
literals AND with what EventStore commits. If a pin changes, the hash chain
definition changed — a contract break, never a refactor.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path

import pytest

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import GENESIS_HASH, EventDraft, EventKind, EventStoreP
from lamarck.eventstore import CanonicalError, EventStore, canonical_bytes

# ----------------------------------------------------------- 3-event fixture

# Hand-written canonical envelopes (keys codepoint-sorted: actor, day, kind,
# payload, qi_delta, seq, stones_delta, tick).
ENV0 = (
    '{"actor":"","day":0,"kind":"run_started",'
    '"payload":{"schema_version":1,"world":"golden-valley"},'
    '"qi_delta":0,"seq":0,"stones_delta":0,"tick":0}'
)
ENV1 = (
    '{"actor":"agent:yan-hua","day":0,"kind":"agent_spawned",'
    '"payload":{"qi_max":1200000,"stones":25},'
    '"qi_delta":1200000,"seq":1,"stones_delta":25,"tick":1}'
)
ENV2 = (
    '{"actor":"agent:yan-hua","day":1,"kind":"action",'
    '"payload":{"degraded":false,"tier":2,"type":"experiment"},'
    '"qi_delta":-300,"seq":2,"stones_delta":-2,"tick":0}'
)
ENVS = [ENV0, ENV1, ENV2]

# Pinned chain over the envelopes above, from GENESIS_HASH.
PIN_H0 = "e6a41d65d56eae354949bc52cbfc9353bd54be0879b55d5d7fde727124f22954"
PIN_H1 = "2a5ccc53e71eac6a1b2dc6a4c09daed0abca010c6c96953e4d3eb518b232a865"
PIN_H2 = "22be077c742da54c18d8e5e02de35329743a3f6351e49abc9b7db867c0e1028f"
PINS = [PIN_H0, PIN_H1, PIN_H2]


def _fixture_drafts() -> list[EventDraft]:
    # Payload dicts are deliberately written key-unsorted; canonicalization
    # must sort them into the ENV* literals above.
    return [
        EventDraft(
            day=0,
            tick=0,
            kind=EventKind.RUN_STARTED,
            payload={"world": "golden-valley", "schema_version": 1},
        ),
        EventDraft(
            day=0,
            tick=1,
            kind=EventKind.AGENT_SPAWNED,
            actor="agent:yan-hua",
            payload={"stones": 25, "qi_max": 1_200_000},
            qi_delta=1_200_000,
            stones_delta=25,
        ),
        EventDraft(
            day=1,
            tick=0,
            kind=EventKind.ACTION,
            actor="agent:yan-hua",
            payload={"type": "experiment", "tier": 2, "degraded": False},
            qi_delta=-300,
            stones_delta=-2,
        ),
    ]


def _tamper(db: Path, sql: str, params: tuple[object, ...] = ()) -> None:
    """Adversarial direct write, bypassing the store."""
    con = sqlite3.connect(db)
    try:
        con.execute(sql, params)
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------- chain + head


def test_empty_store_head_and_verify(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        assert store.head() == (-1, GENESIS_HASH)
        assert store.verify_chain() == (-1, GENESIS_HASH)
        assert list(store.scan()) == []
    # reopening an existing empty db recovers the genesis head
    with EventStore(db) as store:
        assert store.head() == (-1, GENESIS_HASH)


def test_pinned_three_event_chain(tmp_path: Path) -> None:
    # 1) independent recomputation of the chain with hashlib alone
    hashes = []
    prev = GENESIS_HASH
    for env in ENVS:
        prev = hashlib.sha256((prev + env).encode("utf-8")).hexdigest()
        hashes.append(prev)
    assert hashes == PINS

    # 2) the serializer reproduces the hand-written envelope literals
    for seq, (draft, env) in enumerate(zip(_fixture_drafts(), ENVS, strict=True)):
        envelope = {
            "seq": seq,
            "day": draft.day,
            "tick": draft.tick,
            "kind": draft.kind.value,
            "actor": draft.actor,
            "payload": draft.payload,
            "qi_delta": draft.qi_delta,
            "stones_delta": draft.stones_delta,
        }
        assert canonical_bytes(envelope).decode("utf-8") == env

    # 3) the store commits the same chain
    with EventStore(tmp_path / "events.db") as store:
        records = [store.append(d) for d in _fixture_drafts()]
        assert [r.hash for r in records] == PINS
        assert [r.seq for r in records] == [0, 1, 2]
        assert store.head() == (2, PIN_H2)
        assert store.verify_chain() == (2, PIN_H2)


def test_head_advances_per_append(tmp_path: Path) -> None:
    with EventStore(tmp_path / "events.db") as store:
        seen = set()
        for i, draft in enumerate(_fixture_drafts()):
            record = store.append(draft)
            assert store.head() == (i, record.hash)
            seen.add(record.hash)
        assert len(seen) == 3  # every event hash distinct


def test_append_returns_exactly_what_scan_yields(tmp_path: Path) -> None:
    with EventStore(tmp_path / "events.db") as store:
        appended = [store.append(d) for d in _fixture_drafts()]
        assert list(store.scan()) == appended


def test_append_normalizes_committed_content(tmp_path: Path) -> None:
    # NFC applies to actor and payload strings; a pre-composed twin draft
    # must produce the identical hash.
    decomposed = EventDraft(
        day=0,
        tick=0,
        kind=EventKind.ACTION,
        actor="agent:e\u0301",
        payload={"text": "cafe\u0301"},
    )
    composed = EventDraft(
        day=0,
        tick=0,
        kind=EventKind.ACTION,
        actor="agent:\u00e9",
        payload={"text": "caf\u00e9"},
    )
    with EventStore(tmp_path / "a.db") as store:
        record = store.append(decomposed)
        assert record.actor == "agent:\u00e9"
        assert record.payload == {"text": "caf\u00e9"}
        assert list(store.scan()) == [record]
    with EventStore(tmp_path / "b.db") as twin:
        assert twin.append(composed).hash == record.hash


# ------------------------------------------------------------- persistence


def test_persistence_across_close_and_reopen(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    extra = EventDraft(
        day=2,
        tick=0,
        kind=EventKind.LEDGER_ADJUST,
        actor="agent:yan-hua",
        payload={"reason": "bounty", "big": 10**30, "note": "cafe\u0301"},
        stones_delta=60,
    )
    with EventStore(db) as store:
        records = [store.append(d) for d in [*_fixture_drafts(), extra]]
        head = store.head()
    with EventStore(db) as store:
        assert store.head() == head
        assert store.verify_chain() == head
        assert list(store.scan()) == records
        # the chain continues from the recovered head
        next_record = store.append(_fixture_drafts()[0].model_copy(update={"day": 3}))
        assert next_record.seq == 4
        assert store.verify_chain() == store.head()


def test_reopen_rejects_noncontiguous_log(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        for d in _fixture_drafts():
            store.append(d)
    _tamper(db, "DELETE FROM events WHERE seq = 1")
    with pytest.raises(LamarckAssertionError, match="contiguous"):
        EventStore(db)


# ------------------------------------------------------------------- batch


def test_batch_commits_atomically(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        with store.batch():
            for d in _fixture_drafts():
                store.append(d)
        assert store.head() == (2, PIN_H2)
    with EventStore(db) as store:  # committed durably
        assert store.head() == (2, PIN_H2)
        assert store.verify_chain() == (2, PIN_H2)


def test_batch_rolls_back_db_and_head_on_exception(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    drafts = _fixture_drafts()
    with EventStore(db) as store:
        committed = store.append(drafts[0])
        head_before = store.head()
        with pytest.raises(RuntimeError, match="boom"), store.batch():
            store.append(drafts[1])
            store.append(drafts[2])
            # inside the batch, this store sees its own uncommitted rows
            assert store.head()[0] == 2
            assert len(list(store.scan())) == 3
            raise RuntimeError("boom")
        assert store.head() == head_before
        assert list(store.scan()) == [committed]
        # chain continues correctly from the restored head
        record = store.append(drafts[1])
        assert record.seq == 1
        assert store.verify_chain() == store.head()
    with EventStore(db) as store:
        assert store.head()[0] == 1
        assert store.verify_chain() == store.head()


def test_batch_is_not_reentrant(tmp_path: Path) -> None:
    with EventStore(tmp_path / "events.db") as store:
        with store.batch():
            store.append(_fixture_drafts()[0])
            with pytest.raises(LamarckAssertionError, match="reentrant"), store.batch():
                pass  # pragma: no cover
        # the outer batch still committed
        assert store.head()[0] == 0
        assert store.verify_chain() == store.head()


def test_close_inside_batch_asserts_and_rolls_back(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    with pytest.raises(LamarckAssertionError, match="inside an open batch"), store.batch():
        store.append(_fixture_drafts()[0])
        store.close()
    assert store.head() == (-1, GENESIS_HASH)  # rolled back, still open
    store.append(_fixture_drafts()[0])
    assert store.verify_chain() == store.head()
    store.close()


def test_failed_append_leaves_store_unchanged(tmp_path: Path) -> None:
    bad = EventDraft(day=0, tick=0, kind=EventKind.ACTION, payload={"score": 0.5})
    with EventStore(tmp_path / "events.db") as store:
        good = store.append(_fixture_drafts()[0])
        with pytest.raises(CanonicalError, match="float"):
            store.append(bad)  # autocommit path
        assert store.head() == (0, good.hash)
        with pytest.raises(CanonicalError, match="float"), store.batch():
            store.append(_fixture_drafts()[1])
            store.append(bad)  # batch path: whole batch rolls back
        assert store.head() == (0, good.hash)
        assert list(store.scan()) == [good]
        assert store.verify_chain() == store.head()


# -------------------------------------------------------------------- scan


def test_scan_from_seq_slicing(tmp_path: Path) -> None:
    with EventStore(tmp_path / "events.db") as store:
        with store.batch():
            for i in range(10):
                store.append(EventDraft(day=0, tick=i, kind=EventKind.ACTION, payload={"i": i}))
        assert [r.seq for r in store.scan()] == list(range(10))
        assert [r.seq for r in store.scan(from_seq=4)] == [4, 5, 6, 7, 8, 9]
        assert [r.tick for r in store.scan(from_seq=9)] == [9]
        assert list(store.scan(from_seq=10)) == []


# ---------------------------------------------------------------- lifecycle


def test_closed_store_rejects_every_operation(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.db")
    store.close()
    store.close()  # idempotent by design
    draft = _fixture_drafts()[0]
    with pytest.raises(LamarckAssertionError, match="closed"):
        store.append(draft)
    with pytest.raises(LamarckAssertionError, match="closed"):
        store.head()
    with pytest.raises(LamarckAssertionError, match="closed"):
        store.scan()
    with pytest.raises(LamarckAssertionError, match="closed"):
        store.verify_chain()
    with pytest.raises(LamarckAssertionError, match="closed"), store.batch():
        pass  # pragma: no cover


def test_satisfies_eventstore_protocol_shape(tmp_path: Path) -> None:
    # The mypy-time structural proof lives in store._proves_protocol; this
    # is the runtime smoke that every protocol member exists and is callable.
    with EventStore(tmp_path / "events.db") as store:
        proto: EventStoreP = store
        for name in ("append", "batch", "scan", "head"):
            assert callable(getattr(proto, name))


# ---------------------------------------------------------- tamper evidence


def test_verify_detects_payload_value_tampering(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        for d in _fixture_drafts():
            store.append(d)
        # canonical-form payload with a changed value: only the hash betrays it
        _tamper(db, "UPDATE events SET payload = ? WHERE seq = 1", ('{"qi_max":999,"stones":25}',))
        with pytest.raises(LamarckAssertionError, match="hash chain broken"):
            store.verify_chain()


def test_verify_detects_noncanonical_payload_bytes(tmp_path: Path) -> None:
    # Same logical content, non-canonical spelling (added spaces): the hash
    # over the re-parsed content would still match, so only the byte-identity
    # round-trip check can catch it.
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        for d in _fixture_drafts():
            store.append(d)
        spaced = '{"qi_max": 1200000, "stones": 25}'
        _tamper(db, "UPDATE events SET payload = ? WHERE seq = 1", (spaced,))
        with pytest.raises(LamarckAssertionError, match="not canonical"):
            store.verify_chain()


def test_verify_detects_delta_tampering(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        for d in _fixture_drafts():
            store.append(d)
        _tamper(db, "UPDATE events SET qi_delta = -1 WHERE seq = 2")
        with pytest.raises(LamarckAssertionError, match="hash chain broken"):
            store.verify_chain()


def test_verify_detects_mid_log_deletion(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        for d in _fixture_drafts():
            store.append(d)
        _tamper(db, "DELETE FROM events WHERE seq = 1")
        with pytest.raises(LamarckAssertionError, match="sequence gap"):
            store.verify_chain()


def test_verify_detects_tail_truncation_against_memory_head(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        for d in _fixture_drafts():
            store.append(d)
        _tamper(db, "DELETE FROM events WHERE seq = 2")
        with pytest.raises(LamarckAssertionError, match="disagrees with in-memory head"):
            store.verify_chain()


def test_wall_ts_is_never_hashed(tmp_path: Path) -> None:
    db = tmp_path / "events.db"
    with EventStore(db) as store:
        for d in _fixture_drafts():
            store.append(d)
        _tamper(db, "UPDATE events SET wall_ts = 12345.0")
        assert store.verify_chain() == (2, PIN_H2)  # replay is time-independent
    # and reopening over rewritten timestamps changes nothing
    with EventStore(db) as store:
        assert store.head() == (2, PIN_H2)


# --------------------------------------------------------------------- perf


PERF_APPEND_BUDGET_S = 2.0 * (3.0 if os.environ.get("CI") else 1.0)  # CI runners are 2–3× slower


def test_perf_smoke_20k_batched_appends_under_2s(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "perf.db")
    n = 20_000
    drafts = [
        EventDraft(
            day=i // 1000,
            tick=i % 1000,
            kind=EventKind.ACTION,
            actor="agent:perf",
            payload={"i": i, "op": "tick"},
            qi_delta=-3,
        )
        for i in range(n)
    ]
    start = time.perf_counter()
    with store.batch():
        for d in drafts:
            store.append(d)
    elapsed = time.perf_counter() - start
    assert store.head()[0] == n - 1
    assert elapsed < PERF_APPEND_BUDGET_S, (
        f"{n} batched appends took {elapsed:.3f}s (budget {PERF_APPEND_BUDGET_S}s)"
    )
    assert store.verify_chain() == store.head()
    store.close()
