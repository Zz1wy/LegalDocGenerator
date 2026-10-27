"""检索评测指标：Recall@K、MRR、nDCG@K。"""

import math

import pytest

from evaluation.metrics import aggregate, dedupe, ndcg_at_k, percentile, recall_at_k, reciprocal_rank


def test_dedupe_preserves_order():
    assert dedupe(["a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_at_k():
    assert recall_at_k(["a", "b", "x"], {"b", "c"}, k=2) == 0.5
    assert recall_at_k(["a", "b", "c"], {"b", "c"}, k=2) == 0.5
    assert recall_at_k(["a", "b", "c"], {"b", "c"}, k=3) == 1.0
    assert recall_at_k([], {"b"}, k=5) == 0.0
    assert recall_at_k(["a"], set(), k=5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b", "c"], {"c"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["a", "b"], {"c"}) == 0.0
    assert reciprocal_rank(["a"], {"a"}) == 1.0


def test_ndcg_at_k():
    # 命中第 1、3 名：DCG = 1 + 1/log2(4) = 1.5
    # 两个相关条文的理想排序：IDCG = 1 + 1/log2(3)
    expected = 1.5 / (1 + 1 / math.log2(3))
    assert ndcg_at_k(["a", "b", "c"], {"a", "c"}, k=3) == pytest.approx(expected)
    assert ndcg_at_k(["x", "y"], {"a"}, k=2) == 0.0
    assert ndcg_at_k(["a"], set(), k=2) == 0.0


def test_ndcg_dedupes_retrieved_articles():
    # 同一法条被切成两块，不应因为重复占位而抬高 nDCG
    assert ndcg_at_k(["a", "a", "b"], {"a", "b"}, k=3) == pytest.approx(
        ndcg_at_k(["a", "b"], {"a", "b"}, k=3)
    )


def test_percentile():
    assert percentile([], 95) == 0.0
    assert percentile([10], 50) == 10
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)
    assert percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    assert percentile([1, 2, 3, 4], 0) == 1
    assert percentile([1, 2, 3, 4], 100) == 4


def test_aggregate():
    retrieved = {"q1": ["a", "b"], "q2": ["x", "y"]}
    relevant = {"q1": {"a", "z"}, "q2": {"y"}}
    result = aggregate(retrieved, relevant, k=2)
    assert result["queries"] == 2
    assert result["recall@2"] == pytest.approx((0.5 + 1.0) / 2)
    assert result["mrr"] == pytest.approx((1 + 0.5) / 2)
    expected_ndcg = (
        ndcg_at_k(retrieved["q1"], relevant["q1"], 2)
        + ndcg_at_k(retrieved["q2"], relevant["q2"], 2)
    ) / 2
    assert result["ndcg@2"] == pytest.approx(expected_ndcg)
