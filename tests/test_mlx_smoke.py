"""Smoke test: MlxBackend against the real local model. NEVER downloads.

Skips (with reason) unless BOTH hold:

1. ``mlx_lm`` is importable (optional ``[llm]`` extra; absent in CI), and
2. the ``[model].model_id`` from ``configs/valley.toml`` is already present
   in the local Hugging Face cache — checked via
   ``huggingface_hub.try_to_load_from_cache`` / ``scan_cache_dir``. Tests
   must never trigger a network download; a cold cache is a skip, not a
   fetch.

When runnable it exercises the full real path once: envelope prompt in,
chat template + seeded sampling, tiny generation out (max_tokens=8),
asserting non-empty text and positive true token usage both directions.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytest.importorskip("mlx_lm", reason="mlx-lm not installed (optional [llm] extra)")
huggingface_hub = pytest.importorskip(
    "huggingface_hub", reason="huggingface_hub not installed (arrives with mlx-lm)"
)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "valley.toml"


def _model_id() -> str:
    with _CONFIG_PATH.open("rb") as f:
        return str(tomllib.load(f)["model"]["model_id"])


def _cached_locally(model_id: str) -> bool:
    """True iff the model is already in the local HF cache. Never downloads.

    Broad exception handling is deliberate and test-only: any cache-probe
    failure (missing cache dir, corrupt entry) must map to "not cached" —
    the cost of a wrong guess is a skip, never a network fetch.
    """
    try:
        hit = huggingface_hub.try_to_load_from_cache(model_id, "config.json")
        if isinstance(hit, str):
            return True
        return any(repo.repo_id == model_id for repo in huggingface_hub.scan_cache_dir().repos)
    except Exception:
        return False


MODEL_ID = _model_id()

if not _cached_locally(MODEL_ID):
    pytest.skip(
        f"{MODEL_ID} not in the local HF cache; tests never download models",
        allow_module_level=True,
    )


def test_mlx_generate_smoke() -> None:
    from lamarck.contracts import GenParams
    from lamarck.mind.prompt import build_prompt
    from lamarck.serving.backends import MlxBackend

    backend = MlxBackend(MODEL_ID)
    prompt = build_prompt(
        "You are a terse test assistant. Answer in plain words.",
        "Say one short word of greeting.",
    )
    result = backend.generate(prompt, GenParams(max_tokens=8, temp_permille=0, seed=7))
    assert result.text.strip() != ""
    assert result.usage_in > 0
    assert result.usage_out > 0
