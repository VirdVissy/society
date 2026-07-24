"""lamarck.eventstore — canonical JSON and the hash-chained event log.

Public API:

- :func:`canonical_bytes` / :func:`sha256_hex` / :class:`CanonicalError` —
  the one true serializer (contracts: "CANONICAL JSON").
- :class:`EventStore` — append-only SQLite (WAL) log implementing
  ``contracts.EventStoreP`` (contracts: "HASH CHAIN").
"""

from lamarck.eventstore.canonical import CanonicalError, canonical_bytes, sha256_hex
from lamarck.eventstore.store import EventStore

__all__ = ["CanonicalError", "EventStore", "canonical_bytes", "sha256_hex"]
