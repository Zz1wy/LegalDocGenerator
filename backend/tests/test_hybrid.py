"""混合检索：RRF 融合、BM25、降级路径。"""

import pytest

from rag.hybrid import fuse_candidates, hybrid_search, rrf_fuse
from rag.indexer import RetrievalIndex, SimpleBM25, tokenize


class FakeDoc:
    def __init__(self, chunk_id):
        self.metadata = {"chunk_id": chunk_id}


class FakeVectorStore:
    def __init__(self, results):
        self.results = results

    def similarity_search_with_score(self, query, k=20):
        return self.results[:k]


def make_index():
    chunks = [
        {"id": "c1", "text": "借款的利息不得预先在本金中扣除"},
        {"id": "c2", "text": "劳动合同期限三个月以上不满一年的试用期不得超过一个月"},
        {"id": "c3", "text": "当事人可以约定违约金"},
    ]
    tokenized = [tokenize(chunk["text"]) for chunk in chunks]
    return RetrievalIndex(
        chunks=chunks,
        bm25=SimpleBM25(tokenized),
        tokenized=tokenized,
        vectorstore=FakeVectorStore([
            (FakeDoc("c3"), 0.01),
            (FakeDoc("c1"), 0.20),
        ]),
    )


def test_rrf_fuse_orders_by_reciprocal_rank():
    first = [{"id": "a"}, {"id": "b"}]
    second = [{"id": "b"}, {"id": "c"}]
    fused = rrf_fuse([first, second], rrf_k=60)
    assert [item for item, _ in fused] == ["b", "a", "c"]
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["a"] == pytest.approx(1 / 61)
    assert scores["c"] == pytest.approx(1 / 62)


def test_rrf_fuse_handles_empty_rankings():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []
    fused = rrf_fuse([[{"id": "a"}], []])
    assert [item for item, _ in fused] == ["a"]
    assert fused[0][1] == pytest.approx(1 / 61)


def test_rrf_fuse_supports_weights():
    dense = [{"id": "a"}, {"id": "b"}]
    sparse = [{"id": "b"}, {"id": "c"}]
    fused = rrf_fuse([dense, sparse], weights=[1.0, 2.0])
    # b 在两路都靠前；加权后 BM25 第一名的 c 也能超过向量第一名的 a
    assert [item for item, _ in fused] == ["b", "c", "a"]
    scores = dict(fused)
    assert scores["b"] == pytest.approx(1 / 62 + 2 / 61)
    assert scores["c"] == pytest.approx(2 / 62)
    assert scores["a"] == pytest.approx(1 / 61)


def test_rrf_fuse_rejects_weight_mismatch():
    with pytest.raises(ValueError):
        rrf_fuse([[{"id": "a"}], [{"id": "b"}]], weights=[1.0])


def test_fuse_candidates_guarantees_bm25_top_n():
    dense = [
        {"id": "d1", "dense_rank": 1},
        {"id": "d2", "dense_rank": 2},
        {"id": "d3", "dense_rank": 3},
    ]
    sparse = [
        {"id": "s1", "bm25_rank": 1},
        {"id": "s2", "bm25_rank": 2},
        {"id": "s3", "bm25_rank": 3},
        {"id": "s4", "bm25_rank": 4},
    ]
    results = fuse_candidates(
        dense, sparse, top_k=5, bm25_weight=0.0, bm25_guarantee=3
    )
    # 前 3 名固定来自 BM25，剩余名额再由融合结果补齐
    assert [item["id"] for item in results[:3]] == ["s1", "s2", "s3"]
    assert [item["id"] for item in results[3:]] == ["d1", "d2"]
    assert results[0]["bm25_rank"] == 1
    assert results[0]["dense_rank"] is None
    assert results[3]["dense_rank"] == 1


def test_fuse_candidates_dedupes_and_respects_top_k():
    dense = [{"id": "a"}, {"id": "b"}]
    sparse = [{"id": "b"}, {"id": "a"}, {"id": "c"}]
    results = fuse_candidates(
        dense, sparse, top_k=2, bm25_weight=2.0, bm25_guarantee=2
    )
    assert [item["id"] for item in results] == ["b", "a"]
    assert len(results) == 2
    # 两路都命中的块只出现一次
    assert len({item["id"] for item in results}) == 2
    assert results[0]["retrieval_score"] > 0


def test_fuse_candidates_clamps_guarantee_to_top_k():
    dense = []
    sparse = [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]
    results = fuse_candidates(
        dense, sparse, top_k=2, bm25_weight=1.0, bm25_guarantee=5
    )
    assert [item["id"] for item in results] == ["s1", "s2"]


def test_fuse_candidates_handles_empty_channels():
    assert fuse_candidates([], [], top_k=5) == []
    assert [item["id"] for item in fuse_candidates([{"id": "a"}], [], top_k=5)] == ["a"]
    assert [item["id"] for item in fuse_candidates([], [{"id": "b"}], top_k=5)] == ["b"]


def test_tokenize_keeps_legal_negation():
    tokens = tokenize("借款合同没有约定利息")
    assert "利息" in tokens
    assert "的" not in tokens
    # 「不/未/无」这类否定词对法律语义关键，不能被当停用词
    assert "没有" in tokenize("没有约定")


def test_simple_bm25_ranks_exact_term():
    corpus = [["借款", "利息"], ["劳动", "合同"], ["利息", "本金"]]
    bm25 = SimpleBM25(corpus)
    scores = bm25.get_scores(["利息"])
    assert scores[0] > 0
    assert scores[2] > 0
    assert scores[1] == 0


def test_dense_search_maps_metadata_and_ranks():
    index = make_index()
    results = index.dense_search("违约金", k=2)
    assert [item["id"] for item in results] == ["c3", "c1"]
    assert results[0]["dense_rank"] == 1
    assert results[1]["dense_score"] == pytest.approx(0.20)
    assert results[0]["text"].startswith("当事人")


def test_bm25_search_skips_zero_scores():
    index = make_index()
    results = index.bm25_search("借款利息", k=3)
    assert results
    assert results[0]["id"] == "c1"
    assert all(item["bm25_score"] > 0 for item in results)
    assert [item["bm25_rank"] for item in results] == list(range(1, len(results) + 1))


def test_hybrid_search_combines_both_channels():
    index = make_index()
    results = hybrid_search("借款利息", top_k=2, candidate_k=3, index=index)
    assert results
    assert results[0]["id"] == "c1"  # 向量第 2 + BM25 第 1，RRF 后应排第一
    assert results[0]["dense_rank"] == 2
    assert results[0]["bm25_rank"] == 1
    assert all("retrieval_score" in item for item in results)


def test_hybrid_search_falls_back_to_dense(monkeypatch):
    index = make_index()

    def boom(query, k=20):
        raise RuntimeError("bm25 down")

    monkeypatch.setattr(index, "bm25_search", boom)
    results = hybrid_search("违约金", top_k=1, candidate_k=2, index=index)
    assert [item["id"] for item in results] == ["c3"]


def test_hybrid_search_falls_back_to_bm25(monkeypatch):
    index = make_index()

    def boom(query, k=20):
        raise RuntimeError("vector down")

    monkeypatch.setattr(index, "dense_search", boom)
    results = hybrid_search("借款利息", top_k=1, candidate_k=3, index=index)
    assert results[0]["id"] == "c1"


def test_hybrid_search_returns_empty_when_both_fail(monkeypatch):
    index = make_index()

    def boom(query, k=20):
        raise RuntimeError("down")

    monkeypatch.setattr(index, "dense_search", boom)
    monkeypatch.setattr(index, "bm25_search", boom)
    assert hybrid_search("任意", index=index) == []
