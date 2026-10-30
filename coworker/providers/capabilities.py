"""Per-model capability probe.

A heuristic table for now (refined as we probe real providers/endpoints). Accepts
either bare model names (`gpt-5.5`) or provider-qualified ones (`openai:gpt-5.5`).
"""

from __future__ import annotations

import re
from typing import Optional
from .base import ModelCapabilities

_DYNAMIC_CAPABILITIES: dict[str, ModelCapabilities] = {}

_COMMON_VISION_PATTERNS = (
    "-vl",
    "vision",
    "llava",
    "bakllava",
    "cogvlm",
    "minicpm-v",
    "omni",
    "janus",
    "4v",
    "5v",
)


def register_model_capabilities(model: str, caps: ModelCapabilities) -> None:
    """Dynamically register or override capabilities for a model at runtime.

    Avoids needing git commits when new models are discovered or tested dynamically.
    """
    _DYNAMIC_CAPABILITIES[model.strip().lower()] = caps


def capabilities_for(model: str) -> ModelCapabilities:
    # 1. Curated models answer from the matrix (exact full-id match — including reseller ids
    # like `together:zai-org/GLM-5.2`, whose names defeat the prefix heuristics below).
    from .matrix import entry_for

    entry = entry_for(model)
    if entry is not None:
        return entry.caps

    # 2. Runtime dynamic registrations (e.g. from live /models API discovery or user settings)
    lower_model = model.strip().lower()
    if lower_model in _DYNAMIC_CAPABILITIES:
        return _DYNAMIC_CAPABILITIES[lower_model]

    provider = model.split(":", 1)[0].lower() if ":" in model else ""
    name = model.split(":", 1)[-1].lower()  # strip a provider prefix if present
    if name in _DYNAMIC_CAPABILITIES:
        return _DYNAMIC_CAPABILITIES[name]

    # Vision pattern heuristic
    has_vision_indicator = any(p in name for p in _COMMON_VISION_PATTERNS)

    # Ollama (local) models vary widely and many fake/mishandle parallel tool calls — assume
    # tools work (we only point at tool-capable models) but stay conservative otherwise.
    # Vision is detected from common model naming conventions (-vl, vision, llava, etc.).
    from .local_server import LOCAL_SERVERS

    if provider == "ollama" or provider in LOCAL_SERVERS:
        return ModelCapabilities(
            tools=True, vision=has_vision_indicator, parallel_tool_calls=False, streaming=True
        )

    # Cloud-account providers (custom-added ids; curated ones answered from the matrix).
    # The family segment decides: Claude keeps its native capabilities; everything else
    # stays conservative until probed (Converse tool calling works across families, but
    # parallel calls and vision vary per model).
    if provider in ("bedrock", "vertex"):
        if name.startswith(("claude/", "gemini/")) or has_vision_indicator:
            return ModelCapabilities(
                tools=True, vision=True, pdf=True, parallel_tool_calls=True, streaming=True
            )
        return ModelCapabilities(
            tools=True, vision=False, parallel_tool_calls=False, streaming=True
        )

    # Claude / Gemini (both native): tools + vision + parallel tool calls + streaming. The
    # engine executes parallel calls sequentially and each converter folds the results into
    # the single next user message — exactly what both APIs require.
    if provider in ("anthropic", "gemini"):
        return ModelCapabilities(
            tools=True, vision=True, pdf=True, parallel_tool_calls=True, streaming=True
        )

    # OpenAI reasoning models: tools yes, vision and parallel tool calls constrained.
    if name.startswith(("o1", "o3", "o4", "o5")):
        return ModelCapabilities(
            tools=True, vision=False, parallel_tool_calls=False, streaming=True
        )

    # Modern OpenAI GPT models (gpt-4, gpt-4o, gpt-5, gpt-6, gpt-6.1-sol, gpt-6-astra, future gpt-*):
    # tools + vision + pdf + parallel tool calls + streaming.
    if name.startswith("gpt-"):
        return ModelCapabilities(
            tools=True, vision=True, pdf=True, parallel_tool_calls=True, streaming=True
        )

    # OpenAI-compatible vendors (DeepSeek, Z AI/GLM, Kimi, MiniMax, Qwen, xAI/Grok, Mistral,
    # Upstage/Solar): tool calling + streaming across their current lineups; vision is enabled
    # automatically if model name indicates a multimodal variant (e.g. glm-5v, kimi-vision,
    # qwen-vl), else left off for text flagships.
    if name.startswith(
        (
            "deepseek",
            "glm",
            "kimi",
            "minimax",
            "qwen",
            "grok",
            "mistral",
            "magistral",
            "solar",
        )
    ):
        return ModelCapabilities(
            tools=True, vision=has_vision_indicator, parallel_tool_calls=True, streaming=True
        )

    # Azure OpenAI: the id is the user's deployment name, which may say nothing about
    # the model behind it (the GPT / o-series name checks above still apply when it
    # does). Every current OpenAI model there calls tools in parallel.
    if provider == "azure":
        return ModelCapabilities(
            tools=True, vision=has_vision_indicator, parallel_tool_calls=True, streaming=True
        )

    # Dynamic fallback: if name contains vision indicators, allow vision
    return ModelCapabilities(
        tools=True, vision=has_vision_indicator, parallel_tool_calls=False, streaming=True
    )
