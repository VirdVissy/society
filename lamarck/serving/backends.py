"""Model backends — the text-generation seam (contracts: ``ModelBackendP``).

The ``prompt: str`` handed to any backend is ALWAYS the canonical-JSON
envelope string ``{"system": <system_text>, "template": <TEMPLATE_VERSION>,
"user": <user_text>}`` produced by :mod:`lamarck.mind.prompt`. Backends may
parse it (:class:`MlxBackend` does, to build chat messages) or treat it as
an opaque script key (:class:`ScriptedBackend` does).

Division of labour (contracts: "PHASE-1 LIVE SEMANTICS"):

- Backends return RAW text in ``GenResult``; the runner applies
  :func:`sanitize_model_text` before anything is committed. Sanitized,
  NFC-normalized committed text is the truth consumed by parsers.
- Backends implement NO policy: no retries, no fallbacks, no degradation.
  Errors propagate; the runner owns retry/forfeit semantics.
- Usage is true token counts for real backends; :class:`ScriptedBackend`
  synthesizes ``ceil(len/4)`` (the exact formula is pinned in tests).

``MlxBackend`` keeps every mlx import lazy inside ``__init__`` — ``mlx-lm``
is an optional extra (``[llm]``) and is absent in CI. The float conversion
``temp_permille / 1000`` happens at the mlx boundary only and is never
serialized (house no-floats rule applies to serialized state, not to a
third-party sampler's argument).

mlx-lm API surface coded against (mlx-lm >= 0.24):

- ``mlx_lm.load(model_id) -> (model, tokenizer)``
- ``mlx_lm.generate(model, tokenizer, prompt: str | list[int], *,
  max_tokens: int, sampler, verbose: bool) -> str`` (completion text only)
- ``mlx_lm.sample_utils.make_sampler(temp: float) -> sampler``
- ``mlx.core.random.seed(int)``
- ``tokenizer.apply_chat_template(messages, add_generation_prompt=True,
  tokenize=True[, enable_thinking=False]) -> list[int]`` and
  ``tokenizer.encode(text) -> list[int]``; ``tokenizer.chat_template`` is
  the raw template source (the ``enable_thinking`` capability check).
"""

from __future__ import annotations

import importlib
import json
import sys
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from lamarck.asserts import LMK_ASSERT
from lamarck.contracts import GenParams, GenResult, ModelBackendP, ModelSection

__all__ = [
    "AnthropicBackend",
    "MlxBackend",
    "ScriptedBackend",
    "make_backend",
    "sanitize_model_text",
]

_NUL = "\x00"
_SURROGATE_LO = 0xD800
_SURROGATE_HI = 0xDFFF
_REPLACEMENT = "�"


def sanitize_model_text(text: str) -> str:
    """Make raw model output safe for canonical JSON: lone surrogates become
    U+FFFD and NULs (U+0000) are stripped.

    Any surrogate code point in a Python ``str`` is lone by construction
    (well-formed text never contains code points in U+D800..U+DFFF), so every
    surrogate is replaced. Idempotent: the output contains no surrogates and
    no NULs, and re-sanitizing is the identity (property-tested). The runner
    applies this to backend output; backends return raw text.
    """
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code == 0:
            continue
        if _SURROGATE_LO <= code <= _SURROGATE_HI:
            out.append(_REPLACEMENT)
        else:
            out.append(ch)
    return "".join(out)


def _ceil_div_4(n: int) -> int:
    """Integer ceil(n/4) — no float ever materializes."""
    return -(-n // 4)


class ScriptedBackend:
    """Deterministic CI workhorse: replays a script instead of a model.

    ``responses`` is either a ``deque[str]`` (popped left, FIFO — the script
    plays in order) or a callable ``(prompt, params) -> str``. Usage is
    synthesized as ``usage_in = ceil(len(prompt)/4)``,
    ``usage_out = ceil(len(text)/4)`` (ints; formula pinned in tests).

    Queue exhaustion raises ``IndexError``: an exhausted script is a
    test-authoring bug, never a runtime path, so it is deliberately loud.
    """

    def __init__(self, responses: Callable[[str, GenParams], str] | deque[str]) -> None:
        self._responses = responses

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        if isinstance(self._responses, deque):
            text = self._responses.popleft()  # IndexError on exhaustion: test bug
        else:
            text = self._responses(prompt, params)
        return GenResult(
            text=text,
            usage_in=_ceil_div_4(len(prompt)),
            usage_out=_ceil_div_4(len(text)),
        )


class MlxBackend:
    """Real local generation through mlx-lm (Apple silicon).

    All mlx imports happen lazily in ``__init__`` (optional extra; absent in
    CI). ``generate`` parses the canonical-JSON envelope prompt, applies the
    tokenizer's chat template, seeds ``mx.random`` from ``params.seed``, and
    samples at ``temp_permille / 1000``. Usage counts are true tokenizer
    counts: the templated prompt tokens in, the encoded output text out.
    No retries, no fallbacks — errors propagate (the runner owns policy).
    """

    def __init__(self, model_id: str) -> None:
        mlx_lm = importlib.import_module("mlx_lm")  # lazy: optional [llm] extra
        sample_utils = importlib.import_module("mlx_lm.sample_utils")
        mx = importlib.import_module("mlx.core")
        self._generate: Any = mlx_lm.generate
        self._make_sampler: Any = sample_utils.make_sampler
        self._mx: Any = mx
        self.model_id = model_id
        self._model, self._tokenizer = mlx_lm.load(model_id)

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        envelope = json.loads(prompt)
        LMK_ASSERT(
            isinstance(envelope, dict) and set(envelope) == {"system", "template", "user"},
            "prompt is not the canonical envelope {system, template, user}",
            got=str(prompt)[:120],
        )
        messages = [
            {"role": "system", "content": envelope["system"]},
            {"role": "user", "content": envelope["user"]},
        ]
        # Capability check (never try/except-pass): only pass enable_thinking
        # when the chat template actually consumes it (Qwen3 family does).
        template_kwargs: dict[str, Any] = {}
        chat_template = getattr(self._tokenizer, "chat_template", None)
        if isinstance(chat_template, str) and "enable_thinking" in chat_template:
            template_kwargs["enable_thinking"] = False
        prompt_tokens = self._tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            **template_kwargs,
        )
        self._mx.random.seed(params.seed)
        sampler = self._make_sampler(temp=params.temp_permille / 1000)  # float: mlx boundary only
        text = self._generate(
            self._model,
            self._tokenizer,
            prompt=prompt_tokens,
            max_tokens=params.max_tokens,
            sampler=sampler,
            verbose=False,
        )
        return GenResult(
            text=str(text),
            usage_in=len(prompt_tokens),
            usage_out=len(self._tokenizer.encode(str(text))),
        )


class AnthropicBackend:
    """Cloud generation through the Anthropic API (optional ``[api]`` extra).

    Same envelope contract as :class:`MlxBackend`; the sim's determinism is
    unaffected because determinism is record/replay — the committed LLM_CALL
    response is the truth, and replay never re-calls any backend. Usage is
    the API's own billing counts (``usage.input_tokens`` /
    ``usage.output_tokens``), so qi cost equals real spend.

    Per-model request rules (the 4.7+/Sonnet-5 API surface rejects
    non-default sampling and runs adaptive thinking by default; older tiers
    accept temperature and have no thinking unless asked):

    - ``temperature`` is passed as ``temp_permille / 1000`` ONLY for models
      outside ``_NO_SAMPLING_PREFIXES``; for those models it is omitted.
    - ``thinking: {"type": "disabled"}`` is passed ONLY for models in
      ``_DISABLE_THINKING_PREFIXES`` (adaptive-by-default tiers that accept
      an explicit disable). Fable-tier models reject explicit disable and
      are not intended targets here.
    - ``GenParams.seed`` is inert (the API has no sampling seed); it is
      still recorded in the LLM_CALL payload for uniformity.

    Empty or truncated responses (refusal / max_tokens stop reasons) come
    back as ordinary text for the runner's parse-retry-forfeit protocol —
    no special-casing, no policy here.

    TRANSIENT-ERROR PATIENCE (the one exception to "no policy"): a
    multi-hour society run makes thousands of calls, so a sustained 429/529
    window WILL eventually outlast the SDK's internal retries
    (``max_retries=5``, ~a minute of backoff). ``generate`` therefore wraps
    the call in an outer patience loop — up to ``_OUTER_ATTEMPTS`` tries,
    sleeping ``min(120, 15 * 2^k)`` seconds between them (~12 minutes of
    total tolerance) and logging each wait to stderr. Only transient
    classes are retried: connection errors, 408/409/429, and >= 500.
    Permanent errors (400 bad request, 401 auth, 404) propagate
    immediately — the acceptance-run 529 death motivated this; a billing
    400 must still fail fast. Content is never affected: a retried call
    either eventually returns a response (recorded as truth in the chain)
    or the run halts as before.
    """

    _NO_SAMPLING_PREFIXES = (
        "claude-sonnet-5",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-fable",
    )
    _DISABLE_THINKING_PREFIXES = ("claude-sonnet-5",)
    _OUTER_ATTEMPTS = 8
    _RETRYABLE_STATUSES = frozenset({408, 409, 429}) | frozenset(range(500, 600))

    def __init__(self, model_id: str) -> None:
        anthropic = importlib.import_module("anthropic")  # lazy: optional [api] extra
        # Zero-arg client: credentials resolve from the environment
        # (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an `ant auth login`
        # profile). Never hardcode or log a key.
        self._client: Any = anthropic.Anthropic(max_retries=5)
        self._err_status: type[Exception] = anthropic.APIStatusError
        self._err_connection: type[Exception] = anthropic.APIConnectionError
        self.model_id = model_id

    def _is_transient(self, exc: Exception) -> bool:
        if isinstance(exc, self._err_connection):
            return True
        if isinstance(exc, self._err_status):
            return getattr(exc, "status_code", 0) in self._RETRYABLE_STATUSES
        return False

    def _create_with_patience(self, request: dict[str, Any]) -> Any:
        for attempt in range(self._OUTER_ATTEMPTS):
            try:
                return self._client.messages.create(**request)
            except Exception as exc:
                if not self._is_transient(exc) or attempt == self._OUTER_ATTEMPTS - 1:
                    raise
                delay = min(120, 15 * 2**attempt)
                print(
                    f"[serving] transient API error ({type(exc).__name__}); "
                    f"outer retry {attempt + 1}/{self._OUTER_ATTEMPTS - 1} in {delay}s",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def generate(self, prompt: str, params: GenParams) -> GenResult:
        envelope = json.loads(prompt)
        LMK_ASSERT(
            isinstance(envelope, dict) and set(envelope) == {"system", "template", "user"},
            "prompt is not the canonical envelope {system, template, user}",
            got=str(prompt)[:120],
        )
        request: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": params.max_tokens,
            "system": envelope["system"],
            "messages": [{"role": "user", "content": envelope["user"]}],
        }
        if not self.model_id.startswith(self._NO_SAMPLING_PREFIXES):
            request["temperature"] = params.temp_permille / 1000  # API boundary only
        if self.model_id.startswith(self._DISABLE_THINKING_PREFIXES):
            request["thinking"] = {"type": "disabled"}
        response = self._create_with_patience(request)
        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return GenResult(
            text=text,
            usage_in=int(response.usage.input_tokens),
            usage_out=int(response.usage.output_tokens),
        )


def make_backend(section: ModelSection) -> ModelBackendP:
    """Construct the backend named by ``[model].backend``.

    ``"scripted"`` is refused here on purpose: there is no default script,
    so tests must construct :class:`ScriptedBackend` directly with the exact
    responses they mean to replay. ``"mlx"`` loads the real model (and will
    raise ``ModuleNotFoundError`` where mlx-lm is not installed). Anything
    else is a config error.
    """
    if section.backend == "scripted":
        raise ValueError(
            "backend 'scripted' has no default script: construct "
            "ScriptedBackend(responses=...) directly with the script the test means to replay"
        )
    if section.backend == "mlx":
        return MlxBackend(section.model_id)
    if section.backend == "anthropic":
        return AnthropicBackend(section.model_id)
    raise ValueError(
        f"unknown model backend: {section.backend!r} (expected 'mlx', 'anthropic' or 'scripted')"
    )


def _proves_protocol(
    scripted: ScriptedBackend, mlx: MlxBackend, api: AnthropicBackend
) -> tuple[ModelBackendP, ...]:
    """Compile-time (mypy) proof that all backends satisfy ModelBackendP."""
    return (scripted, mlx, api)
