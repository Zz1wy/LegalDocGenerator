"""生成质量抽检的纯函数测试（不调用 LLM）。"""

from __future__ import annotations

from evaluation.run_generation_eval import field_coverage, score_generation
from rag.citations import build_corpus_lookup


def _chunks():
    return [
        {
            "law": "中华人民共和国劳动合同法",
            "short_name": "劳动合同法",
            "article": "第十九条",
            "text": "劳动合同期限三个月以上不满一年的，试用期不得超过一个月。",
        },
        {
            "law": "中华人民共和国劳动合同法",
            "short_name": "劳动合同法",
            "article": "第二十条",
            "text": "劳动者在试用期的工资不得低于本单位相同岗位最低档工资的百分之八十。",
        },
    ]


def _retrieval(*articles):
    return {
        "citations": [
            {
                "id": f"S{index}",
                "law": "中华人民共和国劳动合同法",
                "short_name": "劳动合同法",
                "article": article,
                "text": "",
            }
            for index, article in enumerate(articles, start=1)
        ]
    }


def test_marker_and_explicit_citation_dedupe():
    score = score_generation(
        "依据[S1]《劳动合同法》第十九条，试用期不得超过一个月。",
        _retrieval("第十九条"),
        build_corpus_lookup(_chunks()),
    )

    assert score["cited_total"] == 1
    assert score["cited_valid"] == 1
    assert score["cited_grounded"] == 1
    assert score["cited_hallucinated"] == 0
    assert score["traceability_rate"] == 1.0
    assert score["grounding_rate"] == 1.0


def test_hallucinated_explicit_citation():
    score = score_generation(
        "依据《劳动合同法》第九百九十九条，用人单位应当支付赔偿金。",
        _retrieval("第十九条"),
        build_corpus_lookup(_chunks()),
    )

    assert score["cited_total"] == 1
    assert score["cited_valid"] == 0
    assert score["cited_hallucinated"] == 1
    assert score["hallucinated_keys"] == ["劳动合同法#999"]


def test_corpus_valid_but_not_grounded_citation():
    score = score_generation(
        "依据《劳动合同法》第二十条，试用期工资不得低于约定工资的百分之八十。",
        _retrieval("第十九条"),
        build_corpus_lookup(_chunks()),
    )

    assert score["cited_total"] == 1
    assert score["cited_valid"] == 1
    assert score["cited_grounded"] == 0
    assert score["cited_external_valid"] == 1
    assert score["external_valid_keys"] == ["劳动合同法#20"]


def test_out_of_range_marker_is_reported():
    score = score_generation(
        "依据[S2]的条文。",
        _retrieval("第十九条"),
        build_corpus_lookup(_chunks()),
    )

    assert score["cited_total"] == 0
    assert score["markers"]["invalid"] == [2]
    assert score["invalid"][0]["kind"] == "marker"


def test_no_citation_returns_none_rates():
    score = score_generation(
        "本合同自双方签字之日起生效。",
        _retrieval("第十九条"),
        build_corpus_lookup(_chunks()),
    )

    assert score["cited_total"] == 0
    assert score["traceability_rate"] is None
    assert score["grounding_rate"] is None


def test_field_coverage_ignores_whitespace_and_long_text():
    coverage = field_coverage(
        {
            "party_a": "示例科技有限公司",
            "salary": "每月 15000 元",
            "claim": "判令被告偿还借款本金人民币 50000 元及逾期利息，并承担本案诉讼费用。",
        },
        "甲方：示例科技有限公司；工资每月15000元。",
    )

    assert coverage["checked"] == 2
    assert coverage["matched"] == 2
    assert coverage["rate"] == 1.0
    assert coverage["missing"] == []


def test_field_coverage_normalizes_currency_and_enumerations():
    coverage = field_coverage(
        {
            "rent": "每月 6000 元",
            "scope": "技术方案、源代码、客户名单",
        },
        "租金为每月人民币6000元；保密信息包括技术方案、源代码和客户名单。",
    )

    assert coverage["checked"] == 2
    assert coverage["matched"] == 2
    assert coverage["missing"] == []


def test_field_coverage_requires_every_enumerated_value():
    coverage = field_coverage(
        {"scope": "技术方案,源代码"},
        "保密信息仅包括技术方案。",
    )

    assert coverage["checked"] == 1
    assert coverage["matched"] == 0
    assert coverage["missing"] == [{"field": "scope", "value": "技术方案,源代码"}]
