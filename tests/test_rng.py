"""Pins and properties for lamarck.engine.rng.

The splitmix64 vectors below are REAL: the seed-0 anchor is the published
reference sequence (Steele et al. / xoshiro test vectors) — the first three
outputs of a splitmix64 generator seeded with 0 are the mix function applied
at states gamma·1, gamma·2, gamma·3 counted from 0, i.e. our
``splitmix64(k * gamma mod 2**64)`` for k = 0, 1, 2. If the k=0 value ever
differs from 0xE220A8397B1DCDAF the implementation is wrong, not the pin.
"""

import hashlib
import random

import pytest

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import MASTER_SEED_DEFAULT
from lamarck.engine.rng import RngStreams, splitmix64

_GAMMA = 0x9E3779B97F4A7C15
_U64 = 1 << 64


def _draws(master_seed: int, name: str, n: int = 5) -> list[int]:
    """First n randrange(1000) draws of a fresh stream (fresh RngStreams)."""
    stream = RngStreams(master_seed).stream(name)
    return [stream.randrange(1000) for _ in range(n)]


# ------------------------------------------------------------------ splitmix64


def test_splitmix64_reference_anchor_seed0_sequence() -> None:
    """Published seed-0 splitmix64 outputs 1..3 (the external ground truth)."""
    assert splitmix64(0 * _GAMMA % _U64) == 0xE220A8397B1DCDAF
    assert splitmix64(1 * _GAMMA % _U64) == 0x6E789E6AA1B965F4
    assert splitmix64(2 * _GAMMA % _U64) == 0x06C45D188009454F


def test_splitmix64_pinned_vectors() -> None:
    assert splitmix64(0) == 0xE220A8397B1DCDAF  # == published seed-0 first output
    assert splitmix64(1) == 0x910A2DEC89025CC1
    assert splitmix64(0xDE5EEDDE5EEDDE5E) == 0xBEBE29A791B68242


def test_splitmix64_output_in_u64_range() -> None:
    for x in (0, 1, 2**32, _U64 - 1):
        assert 0 <= splitmix64(x) < _U64


def test_splitmix64_rejects_out_of_range_input() -> None:
    with pytest.raises(LamarckAssertionError):
        splitmix64(-1)
    with pytest.raises(LamarckAssertionError):
        splitmix64(_U64)


# ------------------------------------------------------------------ RngStreams


def test_stream_first_draws_pinned() -> None:
    """First 5 randrange(1000) draws under the house master seed."""
    assert _draws(MASTER_SEED_DEFAULT, "scheduler") == [90, 430, 276, 48, 793]
    assert _draws(MASTER_SEED_DEFAULT, "stub-universe") == [439, 102, 679, 737, 185]
    assert _draws(MASTER_SEED_DEFAULT, "agent:a1") == [95, 886, 677, 709, 612]


def test_stream_derivation_matches_contract() -> None:
    """Recompute the contract derivation by hand and compare draw-for-draw:
    name_tag = first 8 bytes of sha256(utf8(name)) big-endian;
    stream_seed = splitmix64(master_seed XOR name_tag)."""
    for name in ("scheduler", "stub-universe", "agent:a1"):
        tag = int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "big")
        expected_seed = splitmix64(MASTER_SEED_DEFAULT ^ tag)
        expected = random.Random(expected_seed)
        assert _draws(MASTER_SEED_DEFAULT, name, 10) == [
            expected.randrange(1000) for _ in range(10)
        ]


def test_stream_determinism_same_seed_same_name() -> None:
    assert _draws(MASTER_SEED_DEFAULT, "scheduler", 20) == _draws(
        MASTER_SEED_DEFAULT, "scheduler", 20
    )


def test_stream_memoized_same_object_and_stateful() -> None:
    rngs = RngStreams(MASTER_SEED_DEFAULT)
    s1 = rngs.stream("scheduler")
    assert rngs.stream("scheduler") is s1  # same OBJECT, not an equal clone
    first = s1.randrange(1000)
    assert first == 90  # pinned first draw
    # The memoized object carries state: the next draw continues the sequence.
    assert rngs.stream("scheduler").randrange(1000) == 430


def test_different_names_different_streams() -> None:
    rngs = RngStreams(MASTER_SEED_DEFAULT)
    assert rngs.stream("scheduler") is not rngs.stream("stub-universe")
    assert _draws(MASTER_SEED_DEFAULT, "scheduler", 10) != _draws(
        MASTER_SEED_DEFAULT, "stub-universe", 10
    )
    # Derived seeds themselves differ (not just the draw sequences).
    tags = {
        name: int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
        for name in ("scheduler", "stub-universe", "agent:a1", "agent:a2")
    }
    seeds = {splitmix64(MASTER_SEED_DEFAULT ^ t) for t in tags.values()}
    assert len(seeds) == len(tags)


def test_cross_master_seed_independence() -> None:
    assert _draws(MASTER_SEED_DEFAULT, "scheduler", 10) != _draws(0x1, "scheduler", 10)
    assert _draws(0x1, "scheduler", 10) != _draws(0x2, "scheduler", 10)


def test_master_seed_range_asserted() -> None:
    with pytest.raises(LamarckAssertionError):
        RngStreams(-1)
    with pytest.raises(LamarckAssertionError):
        RngStreams(_U64)
    # Boundary values are fine.
    RngStreams(0)
    RngStreams(_U64 - 1)
