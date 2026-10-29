"""API 层的引用语义判卷接线测试（默认关闭、开启后透传、失败降级）。"""

import asyncio

import pytest

import main


@pytest.fixture(autouse=True)
def _clean_semantic_env(monkeypatch):
    monkeypatch.delenv("CITATION_SEMANTIC_CHECK", raising=False)
    monkeypatch.delenv("SEMANTIC_JUDGE_CONCURRENCY", raising=False)


class FakeIndex:
    def __init__(self, chunks):
        self.chunks = chunks


def test_semantic_payload_disabled_by_default():
    result = asyncio.run(
        main.build_semantic_payload({"citations": []}, "正文", "借条", {})
    )
    assert result == {"enabled": False}


def test_semantic_payload_passes_chunks_and_judge_client(monkeypatch):
    monkeypatch.setenv("CITATION_SEMANTIC_CHECK", "true")
    monkeypatch.setenv("SEMANTIC_JUDGE_CONCURRENCY", "9")  # 会被夹到 4
    # conftest 默认把 RAG 关掉；这条用例要验证接线，单独打开。
    monkeypatch.setattr(main, "RAG_AVAILABLE", True)

    captured = {}

    async def fake_check(content, retrieved, doc_type, fields, client, **kwargs):
        captured.update(
            content=content,
            retrieved=retrieved,
            doc_type=doc_type,
            fields=fields,
            client=client,
            **kwargs,
        )
        return {"enabled": True, "units": [], "summary": {"checked": 0}}

    chunks = [{"article": "第六百七十五条", "text": "借款人应当返还借款。"}]
    monkeypatch.setattr(main, "semantic_check_citations", fake_check)
    monkeypatch.setattr(main, "get_judge_client", lambda: "judge-client")
    monkeypatch.setattr(
        "rag.indexer.get_retrieval_index", lambda: FakeIndex(chunks)
    )

    citations = [{"short_name": "民法典", "article": "第六百七十五条"}]
    result = asyncio.run(
        main.build_semantic_payload(
            {"citations": citations}, "正文", "借条", {"lender": "张三"}
        )
    )

    assert result["enabled"] is True
    assert captured["content"] == "正文"
    assert captured["retrieved"] == citations
    assert captured["doc_type"] == "借条"
    assert captured["fields"] == {"lender": "张三"}
    assert captured["client"] == "judge-client"
    assert captured["chunks"] == chunks
    assert captured["concurrency"] == 4


def test_semantic_payload_degrades_when_judge_fails(monkeypatch):
    monkeypatch.setenv("CITATION_SEMANTIC_CHECK", "1")
    monkeypatch.setattr(main, "RAG_AVAILABLE", True)

    async def boom(*args, **kwargs):
        raise RuntimeError("model down")

    monkeypatch.setattr(main, "semantic_check_citations", boom)
    monkeypatch.setattr(main, "get_judge_client", lambda: "judge-client")
    monkeypatch.setattr(
        "rag.indexer.get_retrieval_index", lambda: FakeIndex([])
    )

    result = asyncio.run(
        main.build_semantic_payload({"citations": []}, "正文", "借条", {})
    )
    assert result["enabled"] is True
    assert result["error"] == "引用语义判卷不可用"
    assert result["units"] == []
    assert result["summary"] is None


def test_semantic_payload_reports_missing_rag(monkeypatch):
    monkeypatch.setenv("CITATION_SEMANTIC_CHECK", "on")
    monkeypatch.setattr(main, "RAG_AVAILABLE", False)

    result = asyncio.run(
        main.build_semantic_payload({"citations": []}, "正文", "借条", {})
    )
    assert result["enabled"] is True
    assert result["error"] == "RAG 模块不可用"


def test_semantic_check_flag_parsing(monkeypatch):
    for value in ("1", "true", "TRUE", " yes ", "on"):
        monkeypatch.setenv("CITATION_SEMANTIC_CHECK", value)
        assert main._semantic_check_enabled() is True
    for value in ("", "0", "false", "off", "no"):
        monkeypatch.setenv("CITATION_SEMANTIC_CHECK", value)
        assert main._semantic_check_enabled() is False
