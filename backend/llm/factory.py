"""根据环境变量选择 LLM Provider。"""

from __future__ import annotations

import os
from typing import Optional

from .base import LLMClient, LLMError
from .ollama import OllamaClient
from .openai_compat import OpenAICompatClient

_OPENAI_COMPATIBLE = {"openai", "openai_compat", "deepseek", "qwen"}

_client: Optional[LLMClient] = None
_judge_client: Optional[LLMClient] = None


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def get_model_name() -> str:
    return _env("LLM_MODEL") or _env("OLLAMA_MODEL") or "qwen2.5:7b"


def _build_client(
    *,
    provider: str,
    model: str,
    timeout: float,
    base_url: str,
    api_key: str,
    key_env_hint: str,
) -> LLMClient:
    provider = (provider or "ollama").lower()
    if provider in _OPENAI_COMPATIBLE:
        if not api_key:
            raise LLMError(
                f"Provider 设为云 API，但未配置 {key_env_hint}。"
                "请在 backend/.env 中填写，或把 Provider 改回 ollama。"
            )
        return OpenAICompatClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=timeout,
        )
    return OllamaClient(base_url=base_url, model=model, timeout=timeout)


def get_llm_client() -> LLMClient:
    """按 LLM_PROVIDER 返回单例客户端。

    - ollama（默认）：本地 Ollama
    - openai / openai_compat / deepseek：OpenAI 兼容 HTTP API
    """
    global _client
    if _client is not None:
        return _client

    provider = _env("LLM_PROVIDER", "ollama") or "ollama"
    _client = _build_client(
        provider=provider,
        model=get_model_name(),
        timeout=float(_env("LLM_TIMEOUT", "120")),
        base_url=_env("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        api_key=_env("LLM_API_KEY"),
        key_env_hint="LLM_API_KEY",
    )
    return _client


def get_judge_client() -> LLMClient:
    """引用语义判卷使用的客户端。

    默认复用生成模型；如果配置了 JUDGE_*，可以单独换一个更强的判卷模型，
    避免「同一个模型生成、同一个模型判自己」的宽松偏差：

        JUDGE_PROVIDER=deepseek
        JUDGE_MODEL=deepseek-chat
        JUDGE_BASE_URL=https://api.deepseek.com/v1
        JUDGE_API_KEY=sk-...

    未配置的项逐项回退到生成配置；``JUDGE_PROVIDER=ollama`` 时也可以只换
    JUDGE_MODEL（例如本地再拉一个更大的模型）。
    """
    global _judge_client
    if _judge_client is not None:
        return _judge_client

    provider = _env("JUDGE_PROVIDER") or _env("LLM_PROVIDER", "ollama") or "ollama"
    model = _env("JUDGE_MODEL") or get_model_name()

    if (provider or "").lower() in _OPENAI_COMPATIBLE:
        base_url = (
            _env("JUDGE_BASE_URL")
            or _env("LLM_BASE_URL", "https://api.deepseek.com/v1")
        )
        api_key = _env("JUDGE_API_KEY") or _env("LLM_API_KEY")
        key_hint = "JUDGE_API_KEY 或 LLM_API_KEY"
    else:
        base_url = _env("JUDGE_BASE_URL") or _env("OLLAMA_HOST", "http://localhost:11434")
        api_key = ""
        key_hint = "JUDGE_API_KEY"

    _judge_client = _build_client(
        provider=provider,
        model=model,
        timeout=float(_env("JUDGE_TIMEOUT") or _env("LLM_TIMEOUT", "120")),
        base_url=base_url,
        api_key=api_key,
        key_env_hint=key_hint,
    )
    return _judge_client


def reset_llm_client() -> None:
    """测试或切换配置后清空单例。"""
    global _client, _judge_client
    _client = None
    _judge_client = None
