"""混合检索：向量召回 + BM25 召回 + RRF 融合。"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Iterable, Optional

from .indexer import RetrievalIndex, get_retrieval_index

RRF_K = 60
# 融合参数来自 77 条金标的消融实验，可用环境变量调整。
# 历史上 nomic-embed-text 的中文向量召回很弱，需要「BM25 Top-4 保底」
# 防止向量噪声把关键词第一名挤下去；换成 bge-m3 后纯向量 Recall@5
# 从 0.40 升到 0.96，保底反而压低了排序质量，因此默认改为等权 RRF。
# 旧策略仍可用 HYBRID_BM25_WEIGHT=2.0、HYBRID_BM25_GUARANTEE=4 复现。
BM25_WEIGHT = float(os.getenv("HYBRID_BM25_WEIGHT", "1.0"))
BM25_GUARANTEE = int(os.getenv("HYBRID_BM25_GUARANTEE", "0"))


def rrf_fuse(
    rankings: Iterable[Iterable[dict]],
    rrf_k: int = RRF_K,
    weights: Optional[Iterable[float]] = None,
) -> list[tuple[str, float]]:
    """Weighted Reciprocal Rank Fusion。

    只使用名次、不混用两路分数，避免「余弦距离」和「BM25 分数」量纲不同
    导致的权重调参问题。k=60 是原论文的常用取值。
    """
    rankings = list(rankings)
    weight_list = list(weights) if weights is not None else [1.0] * len(rankings)
    if len(weight_list) != len(rankings):
        raise ValueError("weights 数量必须与 rankings 一致")

    scores: dict[str, float] = defaultdict(float)
    for weight, ranking in zip(weight_list, rankings):
        for rank, item in enumerate(ranking, start=1):
            scores[item["id"]] += weight / (rrf_k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def fuse_candidates(
    dense: list[dict],
    sparse: list[dict],
    top_k: int = 5,
    bm25_weight: float | None = None,
    bm25_guarantee: int | None = None,
) -> list[dict]:
    """把两路候选融合成最终 top_k（供在线检索与离线评测共用）。

    - BM25 Top-N 保底进入结果；
    - 其余名额按 weighted RRF 补齐。
    """
    if bm25_weight is None:
        bm25_weight = float(os.getenv("HYBRID_BM25_WEIGHT", str(BM25_WEIGHT)))
    if bm25_guarantee is None:
        bm25_guarantee = int(os.getenv("HYBRID_BM25_GUARANTEE", str(BM25_GUARANTEE)))
    if not dense and not sparse:
        return []

    fused = rrf_fuse([dense, sparse], weights=[1.0, bm25_weight])
    fused_scores = dict(fused)
    dense_ranks = {chunk["id"]: chunk.get("dense_rank") for chunk in dense}
    bm25_ranks = {chunk["id"]: chunk.get("bm25_rank") for chunk in sparse}
    by_id = {chunk["id"]: chunk for chunk in dense + sparse}

    results: list[dict] = []
    seen: set[str] = set()

    def add(chunk: dict) -> None:
        chunk_id = chunk["id"]
        if chunk_id in seen or len(results) >= top_k:
            return
        seen.add(chunk_id)
        results.append({
            **chunk,
            "retrieval_score": fused_scores.get(chunk_id, 0.0),
            "dense_rank": dense_ranks.get(chunk_id),
            "bm25_rank": bm25_ranks.get(chunk_id),
        })

    for chunk in sparse[:max(0, min(bm25_guarantee, top_k))]:
        add(chunk)

    for chunk_id, _ in fused:
        if len(results) >= top_k:
            break
        chunk = by_id.get(chunk_id)
        if chunk is not None:
            add(chunk)

    return results


def hybrid_search(
    query: str,
    top_k: int = 5,
    candidate_k: int = 20,
    index: RetrievalIndex | None = None,
    doc_type: str | None = None,
) -> list[dict]:
    """BM25 优先的混合检索。

    先保底 BM25 Top-N（法律术语/条号对精确匹配高度敏感），
    剩余名额按 weighted RRF 从两路候选中补齐。
    这样既不会丢掉 BM25 明确命中的条文，也保留向量召回的语义补充。
    `doc_type` 非空时先按文书类型收窄检索范围（见 rag.scope）。
    """
    index = index or get_retrieval_index()

    dense: list[dict] = []
    sparse: list[dict] = []
    try:
        dense = index.dense_search(query, k=candidate_k, doc_type=doc_type)
    except Exception as exc:
        print(f"⚠️ 向量检索失败，降级为 BM25: {exc}")
    try:
        sparse = index.bm25_search(query, k=candidate_k, doc_type=doc_type)
    except Exception as exc:
        print(f"⚠️ BM25 检索失败，降级为向量检索: {exc}")

    return fuse_candidates(dense, sparse, top_k=top_k)


def dense_search(
    query: str,
    top_k: int = 5,
    index: RetrievalIndex | None = None,
    doc_type: str | None = None,
) -> list[dict]:
    """消融实验用的纯向量基线。"""
    index = index or get_retrieval_index()
    return index.dense_search(query, k=top_k, doc_type=doc_type)
