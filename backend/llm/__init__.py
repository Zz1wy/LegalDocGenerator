"""LLM Provider 抽象：本地 Ollama 与 OpenAI 兼容 API 可切换。"""

from .base import LLMClient, LLMError, LLMResult
from .factory import get_llm_client, get_model_name, reset_llm_client

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMResult",
    "get_llm_client",
    "get_model_name",
    "reset_llm_client",
]
