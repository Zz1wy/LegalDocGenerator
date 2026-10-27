"""Ollama 本地模型客户端。"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator, Optional

import httpx

from .base import LLMClient, LLMError, LLMResult


class OllamaClient(LLMClient):
    def __init__(self, base_url: str, model: str, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _options(
        self,
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> dict:
        options: dict = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        return options

    async def generate(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> LLMResult:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": self._options(temperature, max_tokens),
        }
        if system:
            payload["system"] = system
        if json_mode:
            payload["format"] = "json"

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/api/generate", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"无法连接 Ollama ({self.base_url}): {exc}") from exc

        if response.status_code != 200:
            raise LLMError(f"Ollama 返回 {response.status_code}: {response.text[:200]}")

        data = response.json()
        return LLMResult(
            text=data.get("response", ""),
            model=self.model,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    async def stream(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "options": self._options(temperature, max_tokens),
        }
        if system:
            payload["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", f"{self.base_url}/api/generate", json=payload
                ) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", "ignore")
                        raise LLMError(
                            f"Ollama 返回 {response.status_code}: {body[:200]}"
                        )
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunk = data.get("response", "")
                        if chunk:
                            yield chunk
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama 流式调用失败: {exc}") from exc
