"""按文书类型收窄检索范围。"""

from rag.indexer import RetrievalIndex, SimpleBM25, tokenize
from rag.scope import (
    chapter_names,
    chunk_in_scope,
    chroma_where,
    scope_for,
)


class FakeDoc:
    def __init__(self, chunk_id):
        self.metadata = {"chunk_id": chunk_id}


class FakeVectorStore:
    """按传入顺序返回全部结果，不解释 where；章节过滤由索引层兜底。"""

    def __init__(self, results):
        self.results = results
        self.last_filter = None

    def similarity_search_with_score(self, query, k=20, filter=None):
        self.last_filter = filter
        return self.results[:k]


def chunk(chunk_id, text, short_name, book="", chapter="", article="第一条"):
    return {
        "id": chunk_id,
        "text": text,
        "law": f"中华人民共和国{short_name}",
        "short_name": short_name,
        "book": book,
        "chapter": chapter,
        "article": article,
        "article_num": 1,
    }


SALE = chunk(
    "sale",
    "买卖合同的出卖人应当交付标的物并转移所有权",
    "民法典",
    book="合同编",
    chapter="第二分编　典型合同 / 第九章　买卖合同",
)
LEASE = chunk(
    "lease",
    "租赁合同的承租人应当按照约定的方法使用租赁物",
    "民法典",
    book="合同编",
    chapter="第二分编　典型合同 / 第十四章　租赁合同",
)
FINANCE_LEASE = chunk(
    "finance",
    "融资租赁合同应当采用书面形式",
    "民法典",
    book="合同编",
    chapter="第二分编　典型合同 / 第十五章　融资租赁合同",
)
PROPERTY = chunk(
    "property",
    "不动产物权的设立经依法登记发生效力",
    "民法典",
    book="物权编",
    chapter="第一分编　通则 / 第二章　物权的设立、变更、转让和消灭",
)
COMPANY = chunk(
    "company",
    "股东向股东以外的人转让股权，应当经其他股东过半数同意",
    "公司法",
    article="第八十四条",
)


def test_chapter_names_ignore_sub_books():
    names = chapter_names("第二分编　典型合同 / 第十三章　保证合同 / 第一节　一般规定")
    assert names == ("保证合同", "一般规定")
    assert chapter_names("") == ()


def test_scope_filters_by_law_book_and_chapter():
    scopes = scope_for("房屋买卖合同")
    assert chunk_in_scope(SALE, scopes)
    # 物权编整编允许：过户/共有/登记条文同样是买卖依据
    assert chunk_in_scope(PROPERTY, scopes)
    # 合同编里的其他典型合同不应进入候选
    assert not chunk_in_scope(LEASE, scopes)
    assert not chunk_in_scope(FINANCE_LEASE, scopes)
    assert not chunk_in_scope(COMPANY, scopes)


def test_lease_scope_does_not_match_finance_lease():
    scopes = scope_for("租赁合同")
    assert chunk_in_scope(LEASE, scopes)
    # 「租赁合同」不能通过子串误命中「融资租赁合同」
    assert not chunk_in_scope(FINANCE_LEASE, scopes)


def test_unknown_and_unscoped_doc_types_are_not_filtered():
    assert scope_for("不存在的类型") is None
    # 民事起诉状可能基于合同/侵权/劳动，doc_type 决定不了法律部门
    assert scope_for("民事起诉状") is None
    assert chunk_in_scope(COMPANY, None)


def test_chroma_where_builds_or_of_law_and_book_clauses():
    where = chroma_where(scope_for("房屋买卖合同"))
    assert where == {
        "$or": [
            {
                "$and": [
                    {"short_name": {"$in": ["民法典"]}},
                    {"book": {"$in": ["合同编"]}},
                ]
            },
            {
                "$and": [
                    {"short_name": {"$in": ["民法典"]}},
                    {"book": {"$in": ["物权编"]}},
                ]
            },
        ]
    }
    # 只有法名、没有分编限制时不需要章节后置过滤
    assert chroma_where(scope_for("劳动合同")) == {
        "short_name": {"$in": ["劳动合同法", "劳动法"]}
    }
    assert chroma_where(None) is None


def make_index(chunks, store=None):
    tokenized = [tokenize(item["text"]) for item in chunks]
    return RetrievalIndex(
        chunks=chunks,
        bm25=SimpleBM25(tokenized),
        tokenized=tokenized,
        vectorstore=store or FakeVectorStore([]),
    )


def test_scoped_dense_search_filters_out_other_chapters():
    chunks = [SALE, LEASE, FINANCE_LEASE, PROPERTY, COMPANY]
    store = FakeVectorStore([(FakeDoc(item["id"]), 0.1) for item in chunks])
    index = make_index(chunks, store)

    results = index.dense_search("房屋买卖", k=5, doc_type="房屋买卖合同")
    assert [item["id"] for item in results] == ["sale", "property"]
    # 传给 Chroma 的 where 已经收窄到民法典，避免在错误法律里做 ANN
    assert store.last_filter is not None


def test_scoped_bm25_search_only_returns_in_scope_chunks():
    chunks = [SALE, LEASE, PROPERTY, COMPANY]
    index = make_index(chunks)

    results = index.bm25_search("合同", k=5, doc_type="房屋买卖合同")
    assert [item["id"] for item in results] == ["sale"]
    assert [item["bm25_rank"] for item in results] == [1]

    # 不传 doc_type 时保持旧行为
    all_results = index.bm25_search("合同", k=5)
    assert {item["id"] for item in all_results} == {"sale", "lease"}


def test_scope_mask_is_cached_per_doc_type():
    index = make_index([SALE, LEASE])
    first = index.scope_mask("房屋买卖合同")
    assert index.scope_mask("房屋买卖合同") is first
    assert index.scope_mask("民事起诉状") is None
