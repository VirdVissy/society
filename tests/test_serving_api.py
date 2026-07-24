"""AnthropicBackend unit tests — fully mocked, no network, no key.

The anthropic SDK module is replaced in sys.modules before the lazy import
inside ``AnthropicBackend.__init__`` runs, so these tests exercise the
request-construction rules (envelope mapping, per-model temperature and
thinking rules, usage mapping) without touching the API or requiring the
[api] extra at all.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from lamarck.contracts import GenParams, ModelSection
from lamarck.eventstore.canonical import canonical_bytes
from lamarck.serving.backends import AnthropicBackend, make_backend

PARAMS = GenParams(max_tokens=220, temp_permille=700, seed=1234)


def _envelope(system: str = "sys", user: str = "usr") -> str:
    return canonical_bytes({"system": system, "template": "p1.0", "user": user}).decode()


class _FakeMessages:
    def __init__(self, response: object) -> None:
        self.response = response
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        return self.response


def _response(blocks: list[object], usage_in: int = 100, usage_out: int = 20) -> object:
    return SimpleNamespace(
        content=blocks,
        usage=SimpleNamespace(input_tokens=usage_in, output_tokens=usage_out),
    )


@pytest.fixture
def fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> _FakeMessages:
    messages = _FakeMessages(_response([SimpleNamespace(type="text", text="hello")]))
    module = ModuleType("anthropic")

    class Anthropic:  # noqa: N801 - mirrors the SDK class name
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.messages = messages

    module.Anthropic = Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return messages


def test_envelope_maps_to_system_and_user(fake_anthropic: _FakeMessages) -> None:
    backend = AnthropicBackend("claude-haiku-4-5")
    result = backend.generate(_envelope(system="be Yan Hua", user="day 3"), PARAMS)
    req = fake_anthropic.requests[-1]
    assert req["model"] == "claude-haiku-4-5"
    assert req["system"] == "be Yan Hua"
    assert req["messages"] == [{"role": "user", "content": "day 3"}]
    assert req["max_tokens"] == 220
    assert result.text == "hello"
    assert result.usage_in == 100 and result.usage_out == 20


def test_temperature_passed_on_haiku_omitted_on_sonnet5(fake_anthropic: _FakeMessages) -> None:
    AnthropicBackend("claude-haiku-4-5").generate(_envelope(), PARAMS)
    assert fake_anthropic.requests[-1]["temperature"] == 0.7
    AnthropicBackend("claude-sonnet-5").generate(_envelope(), PARAMS)
    assert "temperature" not in fake_anthropic.requests[-1]
    AnthropicBackend("claude-opus-4-8").generate(_envelope(), PARAMS)
    assert "temperature" not in fake_anthropic.requests[-1]


def test_thinking_disabled_only_on_adaptive_default_tiers(fake_anthropic: _FakeMessages) -> None:
    AnthropicBackend("claude-sonnet-5").generate(_envelope(), PARAMS)
    assert fake_anthropic.requests[-1]["thinking"] == {"type": "disabled"}
    AnthropicBackend("claude-haiku-4-5").generate(_envelope(), PARAMS)
    assert "thinking" not in fake_anthropic.requests[-1]


def test_non_text_blocks_ignored_and_text_concatenated(fake_anthropic: _FakeMessages) -> None:
    fake_anthropic.response = _response(
        [
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text="a"),
            SimpleNamespace(type="text", text="b"),
        ]
    )
    result = AnthropicBackend("claude-haiku-4-5").generate(_envelope(), PARAMS)
    assert result.text == "ab"


def test_empty_content_yields_empty_text(fake_anthropic: _FakeMessages) -> None:
    fake_anthropic.response = _response([], usage_in=50, usage_out=0)
    result = AnthropicBackend("claude-haiku-4-5").generate(_envelope(), PARAMS)
    assert result.text == "" and result.usage_out == 0  # runner's retry path handles it


def test_rejects_non_envelope_prompt(fake_anthropic: _FakeMessages) -> None:
    backend = AnthropicBackend("claude-haiku-4-5")
    with pytest.raises(AssertionError):
        backend.generate('{"not": "an envelope"}', PARAMS)


def test_client_configured_with_retries(fake_anthropic: _FakeMessages) -> None:
    backend = AnthropicBackend("claude-haiku-4-5")
    assert backend._client.kwargs == {"max_retries": 5}  # noqa: SLF001 - white-box pin


def test_make_backend_dispatches_anthropic(fake_anthropic: _FakeMessages) -> None:
    section = ModelSection(
        backend="anthropic",
        model_id="claude-haiku-4-5",
        max_tokens=220,
        reflection_max_tokens=160,
        temp_permille=700,
        seed=1,
        prompt_budget_chars=14000,
    )
    backend = make_backend(section)
    assert isinstance(backend, AnthropicBackend)
    assert backend.model_id == "claude-haiku-4-5"


def test_make_backend_unknown_names_all_three() -> None:
    section = ModelSection(
        backend="gpt4",
        model_id="x",
        max_tokens=1,
        reflection_max_tokens=1,
        temp_permille=0,
        seed=0,
        prompt_budget_chars=1000,
    )
    with pytest.raises(ValueError, match="'mlx', 'anthropic' or 'scripted'"):
        make_backend(section)


def test_live_config_accepts_anthropic_backend(tmp_path) -> None:
    from pathlib import Path

    from lamarck.engine.config import load_live_config

    src = Path("configs/valley-cloud.toml").read_text()
    path = tmp_path / "w.toml"
    path.write_text(src)
    cfg = load_live_config(path)
    assert cfg.model.backend == "anthropic"
    assert cfg.model.model_id == "claude-haiku-4-5"
