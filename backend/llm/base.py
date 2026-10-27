"""LLM 客户端接口与通用数据结构。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator, Optional


class LLMError(RuntimeError):
    """LLM 调用失败（网络、鉴权、模型未加载等）。"""


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: float = 0.0


class LLMClient(ABC):
    """所有模型供应商的最小公共接口。

    应用层只依赖这个接口，因此可以在 Ollama / DeepSeek / 通义 /
    OpenAI 兼容网关之间切换，而不改生成逻辑。
    """

    model: str

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> LLMResult:
        """一次性生成，返回完整文本。"""

    @abstractmethod
    def stream(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """流式生成，逐 token yield 文本片段。"""
