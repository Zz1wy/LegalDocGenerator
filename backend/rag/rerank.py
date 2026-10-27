"""可选的 LLM 重排（列表式 rerank）。

为什么用 LLM 而不是 bge-reranker：
- 本项目的部署目标是「本地 Ollama + 可选云 API」，不强制安装 PyTorch；
- 候选只有 8–10 条，一次 JSON 调用即可完成列表重排；
- 失败时保留 RRF 顺序，不影响主链路。

如果后续有 GPU，可无缝替换成 bge-reranker / Cohere Rerank，
接口保持 `llm_rerank(query, citations, top_k) -> list[dict]`。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

from llm import get_llm_client

_RERANK_PROMPT = """你是法律检索结果重排器。请根据用户需求，判断候选法条的相关性。

用户需求：
{query}

候选法条：
{candidates}

要求：
1. 只按「与用户需求的法律相关性」排序，不要考虑条号大小；
2. 只输出 JSON，格式为 {{"ranking": [3, 1, 5, 2, 4]}}，数字是候选编号；
3. 必须包含所有候选编号，不能编造新编号。

JSON："""


def _parse_ranking(text: str, size: int) -> list[int]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        ranking = data.get("ranking") or data.get("order") or []
    except (json.JSONDecodeError, AttributeError):
        match = re.search(r"\[[\s,\d]+\]", text)
        if not match:
            return []
        try:
            ranking = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []

    result = []
    seen = set()
    for item in ranking:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 1 <= index <= size and index not in seen:
            result.append(index)
            seen.add(index)
    return result


async def llm_rerank(
    query: str,
    citations: list[dict],
    top_k: int = 5,
    client=None,
) -> list[dict]:
    """按 LLM 给出的相关性顺序返回前 top_k 条；失败时保持原顺序。"""
    if len(citations) <= top_k:
        return citations[:top_k]

    candidates = "\n\n".join(
        f"[{index}] 《{item.get('short_name') or item.get('law')}》{item.get('article', '')}\n"
        f"{(item.get('text') or '')[:300]}"
        for index, item in enumerate(citations[:10], start=1)
    )
    prompt = _RERANK_PROMPT.format(query=query, candidates=candidates)

    client = client or get_llm_client()
    timeout = float(os.getenv("RERANK_TIMEOUT", "20"))
    try:
        result = await asyncio.wait_for(
            client.generate(prompt, temperature=0.0, max_tokens=200, json_mode=True),
            timeout=timeout,
        )
    except Exception as exc:
        print(f"⚠️ LLM 重排调用失败: {exc}")
        return citations[:top_k]

    ranking = _parse_ranking(result.text, len(citations[:10]))
    if not ranking:
        return citations[:top_k]

    ordered = [citations[index - 1] for index in ranking]
    used = set(ranking)
    ordered.extend(
        citation
        for index, citation in enumerate(citations[:10], start=1)
        if index not in used
    )
    return ordered[:top_k]
