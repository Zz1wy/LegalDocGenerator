"""根据环境变量选择 LLM Provider。"""

from __future__ import annotations

import os
from typing import Optional

from .base import LLMClient, LLMError
from .ollama import OllamaClient
from .openai_compat import OpenAICompatClient

_client: Optional[LLMClient] = None


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def get_model_name() -> str:
    return _env("LLM_MODEL") or _env("OLLAMA_MODEL") or "qwen2.5:7b"


def get_llm_client() -> LLMClient:
    """按 LLM_PROVIDER 返回单例客户端。

    - ollama（默认）：本地 Ollama
    - openai / openai_compat / deepseek：OpenAI 兼容 HTTP API
    """
    global _client
    if _client is not None:
        return _client

    provider = (_env("LLM_PROVIDER", "ollama") or "ollama").lower()
    model = get_model_name()
    timeout = float(_env("LLM_TIMEOUT", "120"))

    if provider in {"openai", "openai_compat", "deepseek", "qwen"}:
        base_url = _env("LLM_BASE_URL", "https://api.deepseek.com/v1")
        api_key = _env("LLM_API_KEY")
        if not api_key:
            raise LLMError(
                "LLM_PROVIDER 设为云 API，但未配置 LLM_API_KEY。"
                "请在 backend/.env 中填写，或把 LLM_PROVIDER 改回 ollama。"
            )
        _client = OpenAICompatClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
    else:
        _client = OllamaClient(
            base_url=_env("OLLAMA_HOST", "http://localhost:11434"),
            model=model,
            timeout=timeout,
        )
    return _client


def reset_llm_client() -> None:
    """测试或切换配置后清空单例。"""
    global _client
    _client = None
