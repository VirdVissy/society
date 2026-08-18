"""Simultaneous rounds — the wave engine's two load-bearing promises.

1. CONCURRENCY INVARIANCE: the event log is a pure function of
   (config, backend responses); transport concurrency and completion order
   must be unobservable. We run the golden scripted config at concurrency 1
   and at concurrency 8 through a jittering, order-scrambling backend
   wrapper and require byte-identical chain heads.

2. SNAPSHOT SEMANTICS: every perception in a round is snapshotted before
   any of the round's commits, so an utterance spoken in round r is
   invisible to every other agent's round-r prompt and lands no earlier
   than round r+1. This is the deliberate world-rule change of 2026-08-18
   (speech is simultaneous within a round) — this test pins it.
"""

from __future__ import annotations

import json
import random
import threading
import time
from collections import Counter

from lamarck.contracts import EventKind, GenParams, GenResult
from lamarck.eventstore import EventStore, TextsStore
from lamarck.live import run_live
from lamarck.serving import ScriptedBackend
from tests.scripted_llm import make as make_scripted
from tests.test_live_golden import golden_cfg


class _JitterBackend:
    """Thread-safe wrapper that scrambles wave completion order.

    Each call sleeps a small pseudo-random time BEFORE entering the inner
    (locked) ScriptedBackend, so concurrent lanes race and finish out of
    lane order; responses themselves stay deterministic because the inner
    backend keys minds by agent name, not call order."""

    def __init__(self, inner: ScriptedBackend) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self._jitter = random.Random(0x1A5)  # fixed test seed

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        with self._lock:
            delay = self._jitter.random() * 0.003
        time.sleep(delay)
        with self._lock:
            return self._inner.generate(prompt, params)


def _run(tmp_path, name: str, *, concurrency: int, jitter: bool):
    backend = ScriptedBackend(make_scripted())
    if jitter:
        backend = _JitterBackend(backend)  # type: ignore[assignment]
    return run_live(
        golden_cfg(),
        tmp_path / name,
        config_path=None,
        backend=backend,
        difftest_interval=0,
        concurrency_override=concurrency,
    )


def test_concurrency_invariant_chain_head(tmp_path):
    seq = _run(tmp_path, "seq", concurrency=1, jitter=False)
    wave = _run(tmp_path, "wave", concurrency=8, jitter=True)
    assert (wave.head_seq, wave.head_hash) == (seq.head_seq, seq.head_hash)
    assert wave.final_state_sha == seq.final_state_sha
    assert (wave.usage_in_total, wave.usage_out_total) == (
        seq.usage_in_total,
        seq.usage_out_total,
    )


def test_round_snapshot_speech_lands_next_round(tmp_path):
    run_dir = tmp_path / "run"
    run_live(
        golden_cfg(),
        run_dir,
        config_path=None,
        backend=ScriptedBackend(make_scripted()),
        difftest_interval=0,
        concurrency_override=4,
    )
    with EventStore(run_dir / "events.sqlite3") as store:
        events = list(store.scan())
    with TextsStore(run_dir / "events.sqlite3") as texts:
        prompts = {ev.seq: texts.get(ev.seq) for ev in events if ev.kind is EventKind.LLM_CALL}

    # Map every tick-purpose prompt to (actor, day, round) via its header.
    def header_round(user: str) -> tuple[int, int]:
        first = user.split("\n", 1)[0]  # "Day D, round R, tick T."
        parts = first.replace(",", "").replace(".", "").split()
        return int(parts[1]), int(parts[3])

    tick_prompts: list[tuple[str, int, int, str]] = []  # (actor, day, round, user)
    slot_round: dict[tuple[str, int, int], int] = {}  # (actor, day, tick) -> round
    for ev in events:
        if ev.kind is not EventKind.LLM_CALL or ev.payload["purpose"] != "tick":
            continue
        prompt = prompts[ev.seq]
        assert prompt is not None
        user = json.loads(prompt)["user"]
        day, rnd = header_round(user)
        assert day == ev.day
        tick_prompts.append((ev.actor, day, rnd, user))
        slot_round[(ev.actor, ev.day, ev.tick)] = rnd

    all_converse = [
        (ev.actor, ev.day, slot_round[(ev.actor, ev.day, ev.tick)], ev.payload["text"])
        for ev in events
        if ev.kind is EventKind.ACTION
        and ev.payload.get("type") == "converse"
        and len(ev.payload.get("text", "")) >= 12
    ]
    assert all_converse, "the golden run must exercise converse"
    # Scripted minds repeat wording across rounds; only globally-unique texts
    # can pin visibility timing unambiguously.
    text_counts = Counter(text for _, _, _, text in all_converse)
    utterances = [u for u in all_converse if text_counts[u[3]] == 1]
    assert utterances, "need at least one globally-unique utterance to pin timing"

    heard_later = 0
    for speaker, day, rnd, text in utterances:
        for actor, p_day, p_rnd, user in tick_prompts:
            if actor == speaker:
                continue
            if (p_day, p_rnd) == (day, rnd):
                # Same round: the utterance must be invisible — perception
                # snapshots precede every one of the round's commits.
                assert text not in user, (speaker, day, rnd, actor)
            elif (p_day, p_rnd) > (day, rnd) and text in user:
                heard_later += 1
    assert heard_later > 0, "speech must land in some later-round perception"
