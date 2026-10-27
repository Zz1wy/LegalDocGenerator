"""法条引用抽取、规范化与「引用落地」校验。"""

from rag.citations import (
    build_corpus_lookup,
    citation_key,
    extract_citations,
    format_source_block,
    normalize_article,
    normalize_law_name,
    verify_citations,
)


CHUNKS = [
    {
        "law": "中华人民共和国劳动合同法",
        "short_name": "劳动合同法",
        "article": "第八十七条",
        "article_num": 87,
        "book": "",
        "source_url": "https://flk.npc.gov.cn/",
        "text": "用人单位违法解除劳动合同的，应当支付赔偿金。",
    },
    {
        "law": "中华人民共和国民法典",
        "short_name": "民法典",
        "article": "第五百八十五条",
        "article_num": 585,
        "book": "合同编",
        "source_url": "https://flk.npc.gov.cn/",
        "text": "当事人可以约定违约金。",
    },
]


def test_normalize_law_name():
    assert normalize_law_name("《中华人民共和国劳动合同法》") == "劳动合同法"
    assert normalize_law_name("民诉法") == "民事诉讼法"
    assert normalize_law_name("中华人民共和国反不正当竞争法") == "反不正当竞争法"
    assert normalize_law_name(" 民法典 ") == "民法典"


def test_normalize_article():
    assert normalize_article("第八十七条") == (87, "")
    assert normalize_article("第十条之一") == (10, "之一")
    assert normalize_article("第 十 条") == (10, "")


def test_citation_key():
    assert citation_key("中华人民共和国劳动合同法", "第八十七条") == ("劳动合同法", 87, "")


def test_extract_citations():
    text = (
        "依据《中华人民共和国劳动合同法》第八十七条，"
        "以及《民法典》第五百八十五条和《民法典》第六百八十条，"
        "《劳动合同法》第八十七条再次出现。"
    )
    citations = extract_citations(text)
    assert [item["law"] for item in citations] == ["劳动合同法", "民法典", "民法典"]
    assert [item["article"] for item in citations] == ["第八十七条", "第五百八十五条", "第六百八十条"]
    # 重复引用去重
    assert len(citations) == 3


def test_extract_citations_ignores_plain_text_and_bad_names():
    assert extract_citations("第八十七条") == []
    assert extract_citations("《劳动法》") == []
    assert extract_citations("") == []


def test_build_corpus_lookup():
    lookup = build_corpus_lookup(CHUNKS)
    assert ("劳动合同法", 87, "") in lookup
    assert ("民法典", 585, "") in lookup


def test_verify_citations_distinguishes_valid_and_invalid():
    text = "《劳动合同法》第八十七条；《劳动合同法》第九百九十九条；《刑法》第二百六十四条"
    result = verify_citations(text, CHUNKS)
    assert result["checked"] == 3
    assert len(result["valid"]) == 1
    assert result["valid"][0]["article"] == "第八十七条"
    assert result["valid"][0]["source_url"] == "https://flk.npc.gov.cn/"
    assert len(result["invalid"]) == 2
    assert all(item["reason"] for item in result["invalid"])


def test_verify_citations_no_citation():
    result = verify_citations("本协议自双方签字之日起生效。", CHUNKS)
    assert result == {"valid": [], "invalid": [], "checked": 0}


def test_format_source_block():
    citations = [
        {**CHUNKS[0], "short_name": "劳动合同法"},
        {**CHUNKS[1], "short_name": "民法典"},
    ]
    block = format_source_block(citations)
    assert "[S1] 《劳动合同法》第八十七条" in block
    assert "[S2] 《民法典》第五百八十五条（合同编）" in block
    assert "不得编造" in block
    assert "【待核实】" in block
    assert format_source_block([]) == ""
