"""
法律条文检索。

检索策略：
1. 用「文书类型 + 关键字段」构造查询；
2. 向量召回（语义）+ BM25 召回（精确词）各取 candidate_k；
3. RRF 融合；
4. 可选：LLM 重排（RERANK_ENABLED=true）；
5. 返回带法名/条号/来源的引用对象，供 Prompt 与前端展示。
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

from .citations import format_source_block
from .hybrid import dense_search, hybrid_search
from .indexer import get_retrieval_index
from .query import expand_query

TOP_K = 5
CANDIDATE_K = 20

# 字段名中英文映射（让查询更自然）
FIELD_LABELS = {
    "party_a": "甲方", "party_b": "乙方", "term": "期限", "salary": "工资",
    "plaintiff": "原告", "defendant": "被告", "claim": "诉讼请求", "reason": "事实理由",
    "lender": "出借人", "borrower": "借款人", "amount": "金额", "repay_date": "还款日期",
    "landlord": "出租方", "tenant": "承租方", "property": "租赁物", "rent": "租金",
    "husband": "男方", "wife": "女方", "child_custody": "子女抚养",
    "property_split": "财产分割", "transferor": "转让方", "transferee": "受让方",
    "company_name": "公司", "share_ratio": "股权比例", "share_price": "转让价格",
    "employer": "甲方", "employee": "乙方", "confidential_scope": "保密范围",
    "confidential_period": "保密期限", "seller": "卖方", "buyer": "买方",
    "house_address": "房屋地址", "house_area": "房屋面积", "house_price": "成交价格",
    "client": "委托人", "agent": "受托人", "matter": "代理事项",
    "authority": "代理权限", "period": "代理期限",
}

# 不同文书类型对应的最佳法律参考（无检索结果时的兜底提示）
DOC_TYPE_TO_LEGAL_REF = {
    "劳动合同": "劳动法 劳动合同法 劳动合同 试用期 工资 解除合同 经济补偿 竞业限制",
    "民事起诉状": "民事诉讼法 起诉条件 起诉状 诉讼请求 事实与理由 证据 管辖",
    "借条": "民法典 借款合同 借条 利息 还款期限 逾期利息 违约责任",
    "租赁合同": "民法典 租赁合同 出租 承租 租金 租赁期限 维修 买卖不破租赁",
    "离婚协议书": "民法典 婚姻家庭 离婚 子女抚养 抚养费 财产分割 共同债务 冷静期",
    "股权转让协议": "公司法 股权转让 股东 优先购买权 出资 变更登记 公司",
    "保密协议": "民法典 反不正当竞争法 保密义务 商业秘密 竞业限制 违约责任",
    "房屋买卖合同": "民法典 买卖合同 房屋买卖 过户 产权 定金 违约金 税费",
    "委托代理书": "民法典 委托合同 代理 代理权限 代理期限 受托人 委托人 无权代理",
}


def build_query(doc_type: str, fields: dict) -> str:
    """把表单转换成检索查询。字段太少时补上该文书类型的关键词。"""
    parts = [doc_type or ""]
    for key, value in (fields or {}).items():
        if value and str(value).strip():
            parts.append(f"{FIELD_LABELS.get(key, key)} {value}")

    non_empty = [part for part in parts[1:] if part.strip()]
    if len(non_empty) <= 1:
        hint = DOC_TYPE_TO_LEGAL_REF.get(doc_type, "")
        if hint:
            parts.append(hint)

    return " ".join(part for part in parts if part.strip())


def _to_citation(chunk: dict, index: int) -> dict:
    return {
        "id": f"S{index}",
        "law": chunk.get("law", ""),
        "short_name": chunk.get("short_name") or chunk.get("law", ""),
        "book": chunk.get("book", ""),
        "article": chunk.get("article", ""),
        "article_num": int(chunk.get("article_num", 0) or 0),
        "chapter": chunk.get("chapter", ""),
        "text": chunk.get("text", ""),
        "version_date": chunk.get("version_date", ""),
        "effective_date": chunk.get("effective_date", ""),
        "source_url": chunk.get("source_url", ""),
        "retrieval_score": chunk.get("retrieval_score"),
        "dense_rank": chunk.get("dense_rank"),
        "bm25_rank": chunk.get("bm25_rank"),
    }


def _fallback_result(doc_type: str, reason: str = "") -> dict:
    hint = DOC_TYPE_TO_LEGAL_REF.get(doc_type, doc_type)
    text = f"请参考中国现行法律法规中关于「{hint}」的相关规定。"
    return {
        "legal_refs": [text],
        "template_refs": [],
        "citations": [],
        "combined": text,
        "retrieval": {"config": "fallback", "reason": reason},
    }


def _build_result(chunks: list[dict], query: str, config: str) -> dict:
    citations = [_to_citation(chunk, i) for i, chunk in enumerate(chunks, start=1)]
    legal_refs = [citation["text"] for citation in citations]
    return {
        "legal_refs": legal_refs,
        "template_refs": [],
        "citations": citations,
        "combined": format_source_block(citations),
        "retrieval": {
            "config": config,
            "query": query,
            "count": len(citations),
            "candidates": [
                {
                    "id": citation["id"],
                    "law": citation["short_name"],
                    "article": citation["article"],
                    "dense_rank": citation["dense_rank"],
                    "bm25_rank": citation["bm25_rank"],
                }
                for citation in citations
            ],
        },
    }


def retrieve(
    doc_type: str,
    fields: dict,
    top_k: int = TOP_K,
    config: str = "hybrid",
) -> dict:
    """同步检索接口（兼容旧测试与同步调用点）。"""
    query = build_query(doc_type, fields)
    return retrieve_by_query(query, top_k=top_k, config=config, fallback_type=doc_type)


def retrieve_by_query(
    query: str,
    top_k: int = TOP_K,
    config: str = "hybrid",
    fallback_type: str = "",
) -> dict:
    """直接按自然语言查询检索，供评测脚本复用。"""
    original_query = query
    query = expand_query(query)
    try:
        index = get_retrieval_index()
        if config == "dense":
            chunks = dense_search(query, top_k=top_k, index=index)
        else:
            chunks = hybrid_search(query, top_k=top_k, index=index)[:top_k]
        if not chunks:
            return _fallback_result(fallback_type or original_query, "no_hit")
        result = _build_result(chunks, query, config)
        if query != original_query:
            result["retrieval"]["original_query"] = original_query
        return result
    except Exception as exc:
        print(f"⚠️ 检索失败，使用兜底提示: {exc}")
        return _fallback_result(fallback_type or original_query, str(exc))


async def aretrieve(
    doc_type: str,
    fields: dict,
    top_k: int = TOP_K,
    config: str = "hybrid",
    rerank: Optional[bool] = None,
) -> dict:
    """异步检索：把同步的 embedding/Chroma 调用放到线程池，避免阻塞事件循环。"""
    query = build_query(doc_type, fields)

    enabled = rerank
    if enabled is None:
        enabled = os.getenv("RERANK_ENABLED", "false").lower() in ("1", "true", "yes", "on")

    fetch_k = max(top_k * 2, top_k) if enabled else top_k
    result = await asyncio.to_thread(
        retrieve_by_query, query, fetch_k, config, doc_type
    )

    citations = result.get("citations") or []
    if enabled and len(citations) > top_k:
        try:
            from .rerank import llm_rerank

            reranked = await llm_rerank(query, citations, top_k=top_k)
            result = _build_result(reranked, query, f"{config}+rerank")
        except Exception as exc:
            print(f"⚠️ 重排失败，保留 RRF 顺序: {exc}")

    return result


def retrieve_simple(doc_type: str, fields: dict, top_k: int = TOP_K) -> str:
    """兼容旧接口：直接返回拼入 Prompt 的文本。"""
    return retrieve(doc_type, fields, top_k)["combined"]
