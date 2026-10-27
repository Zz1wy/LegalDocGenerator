"""法条级切块与语料元数据。"""

from collections import defaultdict

from rag.corpus import article_number, chinese_to_int
from rag.knowledge_base import (
    CHUNK_SIZE,
    MAX_CHUNK_SIZE,
    chunk_documents,
    load_legal_documents,
)


def test_chinese_numbers():
    assert chinese_to_int("一") == 1
    assert chinese_to_int("十") == 10
    assert chinese_to_int("二十一") == 21
    assert chinese_to_int("一百零八") == 108
    assert chinese_to_int("一千二百五十四") == 1254
    assert article_number("第一千二百五十四条") == 1254
    assert article_number("第十条之一") == 10
    assert article_number("不是条号") == 0


def test_corpus_has_expected_laws():
    docs = load_legal_documents()
    names = {doc["short_name"] for doc in docs}
    assert {"民法典", "劳动合同法", "劳动法", "公司法", "民事诉讼法", "反不正当竞争法"} <= names
    # 民法典五个分编都应载入
    books = {doc["book"] for doc in docs if doc["short_name"] == "民法典"}
    assert {"总则编", "合同编", "婚姻家庭编", "物权编", "侵权责任编"} <= books


def test_chunks_have_citation_metadata():
    chunks = chunk_documents(load_legal_documents())
    legal_chunks = [chunk for chunk in chunks if not chunk["is_template"]]
    assert legal_chunks
    for chunk in legal_chunks:
        assert chunk["law"]
        assert chunk["short_name"]
        assert chunk["article"]
        assert chunk["article_num"] > 0
        assert chunk["source_url"].startswith("https://")
        assert chunk["version_date"]
        assert chunk["text"].strip()


def test_chunk_ids_unique_and_sizes_bounded():
    chunks = chunk_documents(load_legal_documents())
    ids = [chunk["id"] for chunk in chunks]
    assert len(ids) == len(set(ids))
    assert max(len(chunk["text"]) for chunk in chunks) <= MAX_CHUNK_SIZE + 60
    assert min(len(chunk["text"]) for chunk in chunks) > 0
    # 语料里有大量长条文，理应出现「一条多块」
    assert len(chunks) > len({(c["short_name"], c["article"]) for c in chunks})


def test_long_article_chunks_share_citation_key():
    chunks = chunk_documents(load_legal_documents())
    grouped = defaultdict(list)
    for chunk in chunks:
        if chunk["article"]:
            grouped[(chunk["short_name"], chunk["article"])].append(chunk)
    multi = [group for group in grouped.values() if len(group) > 1]
    assert multi
    for group in multi[:20]:
        assert len({chunk["id"] for chunk in group}) == len(group)
        assert len({chunk["article_num"] for chunk in group}) == 1
        assert all(len(chunk["text"]) <= MAX_CHUNK_SIZE + 60 for chunk in group)


def test_known_article_metadata():
    chunks = chunk_documents(load_legal_documents())
    target = next(
        chunk
        for chunk in chunks
        if chunk["short_name"] == "民法典" and chunk["article"] == "第五百八十五条"
    )
    assert target["article_num"] == 585
    assert target["book"] == "合同编"
    assert "违约金" in target["text"]
    assert target["version_date"] == "2020-05-28"
    assert target["effective_date"] == "2021-01-01"


def test_template_is_flagged_and_has_no_article():
    chunks = chunk_documents(load_legal_documents())
    templates = [chunk for chunk in chunks if chunk["is_template"]]
    assert templates
    assert all(not chunk["article"] for chunk in templates)
    assert all(chunk["short_name"] == "保密协议模板" for chunk in templates)


def test_chunk_size_constant_sane():
    assert 200 <= CHUNK_SIZE <= 1000
    assert MAX_CHUNK_SIZE >= CHUNK_SIZE
