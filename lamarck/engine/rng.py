"""Deterministic RNG substreams (contracts: "RNG STREAMS").

Derivation, verbatim from the frozen contract:

    name_tag     = first 8 bytes of sha256(utf8(name)), read big-endian -> u64
    stream_seed  = splitmix64(master_seed XOR name_tag)      (one splitmix64 step)
    stream       = random.Random(stream_seed)

Substreams are derived, never shared, never reseeded mid-run. ``stream(name)``
is memoized: the same name always returns the same ``random.Random`` *object*,
so a stream's state advances monotonically across the whole run. Canonical
Phase-0 stream names: ``"scheduler"``, ``"stub-universe"``, ``"agent:{agent_id}"``.

``splitmix64`` is the reference algorithm (Steele et al., "Fast splittable
pseudorandom number generators"); it must match the pinned vectors in
``tests/test_rng.py`` (anchor: the published seed-0 first output
``0xE220A8397B1DCDAF``). All arithmetic is modulo 2**64; inputs outside the
u64 range are a caller bug (LMK_ASSERT).
"""

from __future__ import annotations

import hashlib
import random

from lamarck.asserts import LMK_ASSERT

_U64_MASK = (1 << 64) - 1
_GOLDEN_GAMMA = 0x9E3779B97F4A7C15
_MIX_1 = 0xBF58476D1CE4E5B9
_MIX_2 = 0x94D049BB133111EB


def splitmix64(x: int) -> int:
    """One step of the reference splitmix64 mix function (u64 -> u64).

    Spec (all arithmetic mod 2**64):
        z = x + 0x9E3779B97F4A7C15
        z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) * 0x94D049BB133111EB
        return z ^ (z >> 31)
    """
    LMK_ASSERT(0 <= x <= _U64_MASK, "splitmix64 input outside u64 range", x=x)
    z = (x + _GOLDEN_GAMMA) & _U64_MASK
    z = ((z ^ (z >> 30)) * _MIX_1) & _U64_MASK
    z = ((z ^ (z >> 27)) * _MIX_2) & _U64_MASK
    return (z ^ (z >> 31)) & _U64_MASK


def _name_tag(name: str) -> int:
    """First 8 bytes of sha256(utf8(name)), read big-endian, as a u64."""
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class RngStreams:
    """Named, memoized RNG substreams derived from one master seed.

    Implements ``RngStreamsP``. Each distinct name gets its own
    ``random.Random`` seeded by one splitmix64 step over
    ``master_seed XOR name_tag``; repeated calls with the same name return
    the identical object (state is shared by design — a stream is a single
    sequence consumed across the whole run, never reseeded).
    """

    def __init__(self, master_seed: int) -> None:
        LMK_ASSERT(
            0 <= master_seed <= _U64_MASK,
            "master_seed outside u64 range",
            master_seed=master_seed,
        )
        self._master_seed = master_seed
        self._streams: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        """Return the memoized stream for ``name``, deriving it on first use."""
        existing = self._streams.get(name)
        if existing is not None:
            return existing
        tag = _name_tag(name)
        LMK_ASSERT(0 <= tag <= _U64_MASK, "name_tag outside u64 range", name=name, tag=tag)
        seed = splitmix64(self._master_seed ^ tag)
        LMK_ASSERT(0 <= seed <= _U64_MASK, "stream_seed outside u64 range", name=name, seed=seed)
        derived = random.Random(seed)
        self._streams[name] = derived
        return derived
