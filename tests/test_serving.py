"""Tests for lamarck.serving.backends and lamarck.eventstore.texts.

The TextsStore tests live here (rather than a file of their own) because
the texts side-table is the serving path's storage half: backends produce
the prompt/usage pair, the runner hashes the prompt into LLM_CALL and lands
the full text in llm_texts. The crucial property proven below: llm_texts is
invisible to every fingerprint — the events table, chain head, and
verify_chain are untouched by any texts operation, including dropping the
whole table.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from collections import deque
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lamarck.asserts import LamarckAssertionError
from lamarck.contracts import EventDraft, EventKind, GenParams, ModelSection
from lamarck.eventstore import EventStore
from lamarck.eventstore.texts import TextsStore
from lamarck.serving.backends import ScriptedBackend, make_backend, sanitize_model_text

PARAMS = GenParams(max_tokens=64, temp_permille=700, seed=42)


def _section(backend: str) -> ModelSection:
    return ModelSection(
        backend=backend,
        model_id="test-model",
        max_tokens=64,
        reflection_max_tokens=32,
        temp_permille=700,
        seed=1,
        prompt_budget_chars=2000,
    )


# ------------------------------------------------------------------ sanitize


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("", ""),
        ("plain text", "plain text"),
        ("\x00", ""),
        ("a\x00b\x00c", "abc"),
        ("\ud800", "�"),
        ("\udfff", "�"),
        ("a\ud800b", "a�b"),
        ("\ud83d\ude00", "��"),  # surrogate PAIR = two lone surrogates, NOT recombined
        ("😀 intact", "😀 intact"),  # real astral char passes through
        ("�", "�"),  # replacement char is a fixed point
        ("mix\x00ed \udc00 case", "mixed � case"),
    ],
)
def test_sanitize_examples(raw: str, clean: str) -> None:
    assert sanitize_model_text(raw) == clean


# Alphabet guaranteed to include the hostile characters: hypothesis's default
# text() excludes surrogates, so splice them in explicitly.
_HOSTILE = st.sampled_from(["\x00", "\ud800", "\udbff", "\udc00", "\udfff", "�", "😀", "丹"])
_TEXT = st.lists(st.one_of(st.characters(), _HOSTILE), max_size=80).map("".join)


@given(_TEXT)
def test_sanitize_idempotent(text: str) -> None:
    once = sanitize_model_text(text)
    assert sanitize_model_text(once) == once


@given(_TEXT)
def test_sanitize_output_always_utf8_encodable(text: str) -> None:
    out = sanitize_model_text(text)
    out.encode("utf-8")  # must never raise
    assert "\x00" not in out
    assert all(not 0xD800 <= ord(ch) <= 0xDFFF for ch in out)


@given(_TEXT)
def test_sanitize_preserves_clean_text(text: str) -> None:
    clean = "".join(ch for ch in text if ord(ch) != 0 and not 0xD800 <= ord(ch) <= 0xDFFF)
    if clean == text:
        assert sanitize_model_text(text) == text


# ----------------------------------------------------------- ScriptedBackend


@pytest.mark.parametrize(
    ("length", "expected"),
    [(0, 0), (1, 1), (2, 1), (3, 1), (4, 1), (5, 2), (8, 2), (9, 3), (100, 25), (101, 26)],
)
def test_scripted_usage_formula_pinned(length: int, expected: int) -> None:
    # usage_in = ceil(len(prompt)/4); usage_out = ceil(len(text)/4). Exact.
    backend = ScriptedBackend(deque(["y" * length]))
    result = backend.generate("x" * length, PARAMS)
    assert result.usage_in == expected
    assert result.usage_out == expected


def test_scripted_deque_fifo_and_exhaustion() -> None:
    backend = ScriptedBackend(deque(['{"action": "rest"}', '{"action": "study"}']))
    first = backend.generate("prompt-a", PARAMS)
    second = backend.generate("prompt-b", PARAMS)
    assert first.text == '{"action": "rest"}'
    assert second.text == '{"action": "study"}'
    with pytest.raises(IndexError):  # exhausted script = test-authoring bug, loud
        backend.generate("prompt-c", PARAMS)


def test_scripted_callable_mode() -> None:
    seen: list[tuple[str, GenParams]] = []

    def script(prompt: str, params: GenParams) -> str:
        seen.append((prompt, params))
        return f"echo:{len(prompt)}:{params.seed}"

    backend = ScriptedBackend(script)
    result = backend.generate("12345678", PARAMS)
    assert result.text == "echo:8:42"
    assert result.usage_in == 2  # ceil(8/4)
    assert result.usage_out == -(-len("echo:8:42") // 4)
    assert seen == [("12345678", PARAMS)]


def test_scripted_returns_raw_text_unsanitized() -> None:
    # Backends return RAW text; sanitization is the runner's job.
    backend = ScriptedBackend(deque(["raw\x00with\ud800junk"]))
    assert backend.generate("p", PARAMS).text == "raw\x00with\ud800junk"


# --------------------------------------------------------------- make_backend


def test_make_backend_scripted_refuses_with_guidance() -> None:
    with pytest.raises(ValueError, match="ScriptedBackend"):
        make_backend(_section("scripted"))


def test_make_backend_unknown_rejected() -> None:
    with pytest.raises(ValueError, match="unknown model backend"):
        make_backend(_section("openai"))


@pytest.mark.skipif(
    importlib.util.find_spec("mlx_lm") is not None,
    reason="mlx_lm installed: constructing MlxBackend would load a real model",
)
def test_make_backend_mlx_requires_mlx_lm() -> None:
    with pytest.raises(ModuleNotFoundError):
        make_backend(_section("mlx"))


# ============================================================== TextsStore ==


def _draft(day: int = 0, tick: int = 0) -> EventDraft:
    return EventDraft(day=day, tick=tick, kind=EventKind.RUN_STARTED, payload={"k": "v"})


def test_texts_put_get_roundtrip(tmp_path: Path) -> None:
    with TextsStore(tmp_path / "run.sqlite3") as texts:
        texts.put(0, '{"system":"s","template":"p1.0","user":"u"}')
        texts.put(7, "second prompt")
        assert texts.get(0) == '{"system":"s","template":"p1.0","user":"u"}'
        assert texts.get(7) == "second prompt"
        assert texts.get(3) is None


def test_texts_put_idempotent_same_content(tmp_path: Path) -> None:
    with TextsStore(tmp_path / "run.sqlite3") as texts:
        texts.put(0, "same prompt")
        texts.put(0, "same prompt")  # no-op, no error
        assert texts.get(0) == "same prompt"


def test_texts_overwrite_different_content_asserts(tmp_path: Path) -> None:
    with TextsStore(tmp_path / "run.sqlite3") as texts:
        texts.put(0, "original")
        with pytest.raises(LamarckAssertionError, match="overwrite"):
            texts.put(0, "different")
        assert texts.get(0) == "original"  # original survives the refused overwrite


def test_texts_negative_seq_asserts(tmp_path: Path) -> None:
    with (
        TextsStore(tmp_path / "run.sqlite3") as texts,
        pytest.raises(LamarckAssertionError, match="seq must be >= 0"),
    ):
        texts.put(-1, "nope")


def test_texts_persist_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "run.sqlite3"
    with TextsStore(path) as texts:
        texts.put(3, "survives reopen")
    with TextsStore(path) as texts:
        assert texts.get(3) == "survives reopen"
        texts.put(3, "survives reopen")  # still idempotent after reopen
        with pytest.raises(LamarckAssertionError):
            texts.put(3, "changed after reopen")


def test_texts_use_after_close_asserts(tmp_path: Path) -> None:
    texts = TextsStore(tmp_path / "run.sqlite3")
    texts.close()
    texts.close()  # idempotent
    with pytest.raises(LamarckAssertionError, match="closed"):
        texts.get(0)
    with pytest.raises(LamarckAssertionError, match="closed"):
        texts.put(0, "x")


def test_texts_share_file_with_eventstore_events_untouched(tmp_path: Path) -> None:
    """The critical property: llm_texts ops never disturb the event log."""
    path = tmp_path / "run.sqlite3"
    with EventStore(path) as store:
        store.append(_draft(0, 0))
        store.append(_draft(0, 1))
        head_before = store.head()
        with TextsStore(path) as texts:  # same file, own connection
            texts.put(0, "prompt zero")
            texts.put(1, "prompt one")
            assert store.head() == head_before  # texts writes moved nothing
            store.append(_draft(1, 0))  # interleaved event append still works
            assert texts.get(0) == "prompt zero"
        assert store.verify_chain() == store.head()  # chain green over 3 events
        assert store.head()[0] == 2


def test_texts_created_first_then_eventstore(tmp_path: Path) -> None:
    # Opposite open order: texts table first, events second. Both coexist.
    path = tmp_path / "run.sqlite3"
    with TextsStore(path) as texts:
        texts.put(0, "early prompt")
    with EventStore(path) as store:
        store.append(_draft())
        assert store.verify_chain() == store.head()
    with TextsStore(path) as texts:
        assert texts.get(0) == "early prompt"


def test_dropping_llm_texts_cannot_change_any_fingerprint(tmp_path: Path) -> None:
    """Documented guarantee, proven: DROP TABLE llm_texts leaves the chain
    head and verify_chain untouched — nothing in llm_texts is ever hashed."""
    path = tmp_path / "run.sqlite3"
    with EventStore(path) as store:
        store.append(_draft(0, 0))
        store.append(_draft(0, 1))
        head = store.head()
    with TextsStore(path) as texts:
        texts.put(0, "prompt zero")
        texts.put(1, "prompt one")
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE llm_texts")
    conn.commit()
    conn.close()
    with EventStore(path) as store:
        assert store.head() == head
        assert store.verify_chain() == head
