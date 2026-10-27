"""检索指标：Recall@K、MRR、nDCG@K。

这里的「相关」以法条为单位：同一法条被切成多个块时只算一次，
避免一篇长条文占满 Top-5 导致指标虚高/虚低。
"""

from __future__ import annotations

import math
from typing import Iterable


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def reciprocal_rank(retrieved: list[str], relevant: set[str]) -> float:
    for rank, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    dcg = 0.0
    for rank, item in enumerate(dedupe(retrieved)[:k], start=1):
        if item in relevant:
            dcg += 1.0 / math.log2(rank + 1)

    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def percentile(values: list[float], p: float) -> float:
    """线性插值分位数（与 numpy.percentile 默认行为一致）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = min(max(p, 0.0), 100.0) / 100 * (len(ordered) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def aggregate(
    retrieved_per_query: dict[str, list[str]],
    relevant_per_query: dict[str, set[str]],
    k: int = 5,
) -> dict:
    recalls = []
    reciprocal_ranks = []
    ndcgs = []
    for query_id, relevant in relevant_per_query.items():
        retrieved = retrieved_per_query.get(query_id, [])
        recalls.append(recall_at_k(retrieved, relevant, k))
        reciprocal_ranks.append(reciprocal_rank(retrieved, relevant))
        ndcgs.append(ndcg_at_k(retrieved, relevant, k))

    count = len(recalls) or 1
    return {
        f"recall@{k}": sum(recalls) / count,
        "mrr": sum(reciprocal_ranks) / count,
        f"ndcg@{k}": sum(ndcgs) / count,
        "queries": len(recalls),
    }
