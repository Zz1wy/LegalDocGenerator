"""引用语义恰当性校验（LLM-as-judge）的单元测试。"""

import asyncio

import pytest

from llm.base import LLMResult
from rag.citations import citation_key
from rag.scope import scope_for
from rag.semantic import (
    JUDGE_SYSTEM_PROMPT,
    build_corpus_chunk_lookup,
    build_corpus_text_lookup,
    build_judge_prompt,
    check_citations,
    document_verdict,
    extract_citation_units,
    extract_context,
    judge_citations,
    parse_judge_response,
    public_units,
    summarize_judgements,
)


CHUNKS = [
    {
        "law": "中华人民共和国民法典",
        "short_name": "民法典",
        "article": "第六百七十条",
        "book": "合同编",
        "chapter": "第十二章 借款合同",
        "chunk_index": 0,
        "text": "借款的利息不得预先在本金中扣除。",
    },
    {
        "law": "中华人民共和国民法典",
        "short_name": "民法典",
        "article": "第六百七十五条",
        "chunk_index": 0,
        "text": "借款人应当按照约定的期限返还借款。",
    },
    {
        "law": "中华人民共和国民法典",
        "short_name": "民法典",
        "article": "第六百七十六条",
        "chunk_index": 0,
        "text": "借款人未按照约定的期限返还借款的，",
    },
    {
        "law": "中华人民共和国民法典",
        "short_name": "民法典",
        "article": "第六百七十六条",
        "chunk_index": 1,
        "text": "应当按照约定或者国家有关规定支付逾期利息。",
    },
]


class FakeClient:
    """按顺序返回预设响应；响应为异常时抛出。"""

    model = "fake-judge"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, prompt, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return LLMResult(text=response, model=self.model)


def run(coro):
    return asyncio.run(coro)


def test_build_corpus_text_lookup_joins_split_article():
    lookup = build_corpus_text_lookup(CHUNKS)
    key = citation_key("民法典", "第六百七十六条")
    assert "未按照约定的期限" in lookup[key]
    assert "支付逾期利息" in lookup[key]


def test_build_corpus_text_lookup_includes_chapter_metadata():
    lookup = build_corpus_text_lookup(CHUNKS)
    key = citation_key("民法典", "第六百七十条")
    assert "所属：合同编" in lookup[key]
    assert "第十二章 借款合同" in lookup[key]


def test_extract_context_keeps_citation_sentence():
    text = "事实与理由。根据《民法典》第六百七十五条，被告应返还借款。特此起诉。"
    start = text.index("《民法典》")
    end = text.index("，被告") + 1
    context = extract_context(text, start, end)
    assert "根据《民法典》第六百七十五条" in context
    assert "被告应返还借款" in context
    assert "特此起诉" not in context


def test_extract_context_extends_when_citation_stands_alone():
    text = "被告到期未还款，应当承担逾期利息。\n法律依据：《民法典》第六百七十六条"
    start = text.index("《民法典》")
    context = extract_context(text, start, len(text))
    assert "被告到期未还款" in context
    assert "第六百七十六条" in context


def test_extract_citation_units_merges_explicit_and_marker():
    text = "根据《民法典》第六百七十五条 [S1]，被告应返还借款。"
    retrieved = [
        {
            "short_name": "民法典",
            "article": "第六百七十五条",
            "text": "借款人应当按照约定的期限返还借款。",
        }
    ]
    units = extract_citation_units(text, retrieved, build_corpus_text_lookup(CHUNKS))
    assert len(units) == 1
    unit = units[0]
    assert unit["law"] == "民法典"
    assert unit["occurrences"] == 2
    assert unit["source"] == "corpus"
    assert "第六百七十五条" in unit["context"]


def test_extract_citation_units_marker_only_uses_retrieved_text():
    text = "被告应返还借款 [S1]。"
    retrieved = [
        {
            "short_name": "民法典",
            "article": "第六百七十五条",
            "text": "借款人应当按照约定的期限返还借款。",
        }
    ]
    units = extract_citation_units(text, retrieved)
    assert len(units) == 1
    assert units[0]["kind"] == "marker"
    assert units[0]["source"] == "retrieval"
    assert "返还借款" in units[0]["article_text"]


def test_extract_citation_units_ignores_out_of_range_marker():
    units = extract_citation_units("正文 [S9]。", [])
    assert units == []


def test_extract_citation_units_flags_scope_mismatch():
    chunk = {
        "law": "中华人民共和国民法典",
        "short_name": "民法典",
        "article": "第七百五十二条",
        "book": "合同编",
        "chapter": "第二分编 典型合同 / 第十五章 融资租赁合同",
        "chunk_index": 0,
        "text": "承租人应当按照约定支付租金。",
    }
    text = "根据《民法典》第七百五十二条，出租人可以解除合同。"
    units = extract_citation_units(
        text,
        [],
        build_corpus_text_lookup([chunk]),
        scopes=scope_for("租赁合同"),
        corpus_chunks=build_corpus_chunk_lookup([chunk]),
    )
    assert units[0]["scope_mismatch"] is True
    assert "[引用]" not in units[0]["context"]  # 标记只在 prompt 里加
    prompt = build_judge_prompt(units[0], "租赁合同", {})
    assert "不在文书类型「租赁合同」的常用范围内" in prompt


def test_build_judge_prompt_contains_context_and_article():
    unit = {
        "law": "民法典",
        "article": "第六百七十条",
        "raw": "《民法典》第六百七十条",
        "context": "根据《民法典》第六百七十条，原告主张逾期利息。",
        "article_text": "借款的利息不得预先在本金中扣除。",
    }
    prompt = build_judge_prompt(unit, "民事起诉状", {"claim": "返还本金及逾期利息"})
    assert "民事起诉状" in prompt
    assert "【引用】《民法典》第六百七十条【/引用】" in prompt
    assert "原告主张逾期利息" in prompt
    assert "借款的利息不得预先在本金中扣除" in prompt
    assert "返还本金及逾期利息" in prompt


def test_parse_judge_response_accepts_plain_json_and_fences():
    assert parse_judge_response(
        '{"claim": "应返还借款", "verdict": "supported", "reason": "条文直接规定返还期限"}'
    ) == {
        "claim": "应返还借款",
        "verdict": "supported",
        "reason": "条文直接规定返还期限",
    }

    parsed = parse_judge_response(
        '```json\n{"verdict": "partial", "reason": "只覆盖部分主张"}\n```'
    )
    assert parsed["verdict"] == "partial"
    assert parsed["claim"] == ""


def test_parse_judge_response_accepts_chinese_alias_and_fallback_text():
    assert parse_judge_response('{"verdict": "部分支持", "reason": "相关"}') == {
        "claim": "",
        "verdict": "partial",
        "reason": "相关",
    }
    parsed = parse_judge_response(
        '结论如下 claim: "应支付逾期利息" verdict: unsupported reason: "用于不同法律关系"'
    )
    assert parsed["verdict"] == "unsupported"
    assert parsed["claim"] == "应支付逾期利息"
    assert parsed["reason"] == "用于不同法律关系"


def test_parse_judge_response_reconciles_reason_and_verdict():
    parsed = parse_judge_response(
        '{"claim": "被告应支付逾期利息", "verdict": "unverifiable", '
        '"reason": "条文与主张无关。"}'
    )
    assert parsed["verdict"] == "unsupported"

    parsed = parse_judge_response(
        '{"claim": "某事", "verdict": "unverifiable", '
        '"reason": "条文原文缺失，无法判断。"}'
    )
    assert parsed["verdict"] == "unverifiable"


def test_parse_judge_response_rejects_unparseable():
    with pytest.raises(ValueError):
        parse_judge_response("我觉得大体上没问题")


def test_judge_citations_marks_supported_and_failure():
    units = [
        {
            "key": "民法典#675",
            "law": "民法典",
            "article": "第六百七十五条",
            "context": "被告应返还借款。",
            "article_text": "借款人应当按照约定的期限返还借款。",
        },
        {
            "key": "民法典#670",
            "law": "民法典",
            "article": "第六百七十条",
            "context": "被告应支付逾期利息。",
            "article_text": "借款的利息不得预先在本金中扣除。",
        },
    ]
    client = FakeClient([
        '{"verdict": "supported", "reason": "直接对应"}',
        RuntimeError("timeout"),
    ])
    results = run(judge_citations(units, "借条", {}, client))
    assert [item["verdict"] for item in results] == ["supported", "unverifiable"]
    assert "timeout" in results[1]["reason"]
    # 判卷统一用 JSON 模式、温度 0，且把系统提示单独传入
    assert client.calls[0]["json_mode"] is True
    assert client.calls[0]["temperature"] == 0
    assert client.calls[0]["system"] == JUDGE_SYSTEM_PROMPT


def test_scope_mismatch_downgrades_supported():
    unit = {
        "key": "民法典#752",
        "law": "民法典",
        "article": "第七百五十二条",
        "context": "根据《民法典》第七百五十二条，出租人可以解除合同。",
        "article_text": "承租人应当按照约定支付租金。",
        "scope_mismatch": True,
    }
    client = FakeClient([
        '{"claim": "出租人可以解除合同", "verdict": "supported", "reason": "措辞相近"}'
    ])
    result = run(judge_citations([unit], "租赁合同", {}, client))[0]
    assert result["judge_verdict"] == "supported"
    assert result["verdict"] == "unsupported"
    assert "章节范围错位" in result["reason"]


def test_missing_article_text_is_unverifiable_without_calling_model():
    units = [{
        "key": "刑法#264",
        "law": "刑法",
        "article": "第二百六十四条",
        "context": "被告构成盗窃罪。",
        "article_text": "",
    }]
    client = FakeClient([])
    results = run(judge_citations(units, "民事起诉状", {}, client))
    assert results[0]["verdict"] == "unverifiable"
    assert client.calls == []


def test_summarize_and_document_verdict():
    judgements = [
        {"key": "a", "law": "民法典", "article": "第一条", "verdict": "supported"},
        {"key": "b", "law": "民法典", "article": "第二条", "verdict": "partial"},
        {"key": "c", "law": "民法典", "article": "第三条", "verdict": "unsupported"},
        {"key": "d", "law": "民法典", "article": "第四条", "verdict": "unverifiable"},
    ]
    summary = summarize_judgements(judgements)
    assert summary["checked"] == 4
    assert summary["scored"] == 3
    assert summary["counts"] == {
        "supported": 1, "partial": 1, "unsupported": 1, "unverifiable": 1
    }
    assert summary["supported_rate"] == pytest.approx(1 / 3)
    assert summary["document_verdict"] == "partial"
    assert {item["verdict"] for item in summary["needs_review"]} == {
        "partial", "unsupported"
    }

    assert document_verdict({"supported": 2, "partial": 0, "unsupported": 0}) == "supported"
    assert document_verdict({"supported": 0, "partial": 0, "unsupported": 2}) == "unsupported"
    assert document_verdict({"supported": 0, "partial": 0, "unsupported": 0}) == "no_citation"


def test_public_units_drops_article_text():
    units = [{
        "key": "民法典#675",
        "law": "民法典",
        "article": "第六百七十五条",
        "kind": "explicit",
        "verdict": "supported",
        "reason": "直接对应",
        "context": "被告应返还借款。",
        "occurrences": 1,
        "article_text": "很长的条文原文",
        "position": 10,
    }]
    public = public_units(units)
    assert public[0]["key"] == "民法典#675"
    assert "article_text" not in public[0]
    assert "position" not in public[0]


def test_check_citations_end_to_end_with_fake_client():
    content = "根据《民法典》第六百七十五条，被告应返还借款。"
    retrieved = [
        {
            "short_name": "民法典",
            "article": "第六百七十五条",
            "text": "借款人应当按照约定的期限返还借款。",
        }
    ]
    client = FakeClient(['{"verdict": "supported", "reason": "直接对应"}'])
    result = run(check_citations(
        content,
        retrieved,
        "民事起诉状",
        {"claim": "返还借款"},
        client,
        build_corpus_text_lookup(CHUNKS),
    ))
    assert result["enabled"] is True
    assert len(result["units"]) == 1
    assert result["units"][0]["verdict"] == "supported"
    assert result["summary"]["document_verdict"] == "supported"
