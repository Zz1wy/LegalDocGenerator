"""
检索索引：Chroma 向量库 + BM25 关键词索引的懒加载单例。

为什么需要 BM25：
- 向量检索擅长语义相似，但对「第八十二条」这类精确条号、专有名词不敏感；
- BM25 正好相反，擅长精确词命中；
- 二者用 RRF 融合，通常比任何单一路径都稳。

索引只在第一次检索时构建，后续请求复用，避免旧版每次请求重建
Chroma/Embedding 客户端（实测单次检索 4–5 秒）。
"""

from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field

from .knowledge_base import (
    CHROMA_DB_DIR,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    OLLAMA_HOST,
    build_knowledge_base,
)

try:
    import jieba
    jieba.setLogLevel(20)
except ImportError:  # pragma: no cover - 无 jieba 时退化为字符二元组
    jieba = None

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover
    BM25Okapi = None

# 只去掉高频虚词；保留「不/未/无」等对法律语义有影响的词
_STOPWORDS = {
    "的", "了", "和", "与", "及", "或", "是", "在", "有", "对", "为", "其",
    "中", "等", "之", "而", "被", "将", "把", "由", "从", "到", "以", "并",
    "也", "都", "就", "该", "本", "此", "这", "那", "一个", "以及",
}


def tokenize(text: str) -> list[str]:
    """中文分词 + 轻量归一化。"""
    text = (text or "").lower()
    if jieba is not None:
        raw = jieba.lcut_for_search(text)
    else:
        # 无 jieba 时：英文/数字按词，中文按二元组，至少保证可检索
        raw = re.findall(r"[a-z0-9]+|[一-鿿]{1,2}", text)
    tokens = []
    for token in raw:
        token = token.strip()
        if not token or token in _STOPWORDS:
            continue
        if not re.search(r"[\w一-鿿]", token):
            continue
        tokens.append(token)
    return tokens


class SimpleBM25:
    """rank_bm25 未安装时的纯 Python 兜底实现（够用即可）。"""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.doc_len) / len(corpus)) if corpus else 1.0
        self.doc_freqs: list[dict[str, int]] = []
        document_freq: dict[str, int] = {}

        for doc in corpus:
            freqs: dict[str, int] = {}
            for term in doc:
                freqs[term] = freqs.get(term, 0) + 1
            self.doc_freqs.append(freqs)
            for term in freqs:
                document_freq[term] = document_freq.get(term, 0) + 1

        total = max(len(corpus), 1)
        self.idf = {
            term: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for term, freq in document_freq.items()
        }

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * len(self.corpus)
        for term in query:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for index, freqs in enumerate(self.doc_freqs):
                freq = freqs.get(term, 0)
                if not freq:
                    continue
                norm = self.k1 * (
                    1 - self.b + self.b * self.doc_len[index] / (self.avgdl or 1)
                )
                scores[index] += idf * freq * (self.k1 + 1) / (freq + norm)
        return scores


def _build_bm25(tokenized: list[list[str]]):
    if BM25Okapi is not None:
        return BM25Okapi(tokenized)
    return SimpleBM25(tokenized)


@dataclass
class RetrievalIndex:
    chunks: list[dict]
    bm25: object
    tokenized: list[list[str]]
    vectorstore: object
    chunk_by_id: dict[str, dict] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.chunk_by_id:
            self.chunk_by_id = {chunk["id"]: chunk for chunk in self.chunks}

    @property
    def size(self) -> int:
        return len(self.chunks)

    def dense_search(self, query: str, k: int = 20) -> list[dict]:
        docs = self.vectorstore.similarity_search_with_score(query, k=k)
        results = []
        for rank, (document, distance) in enumerate(docs, start=1):
            chunk_id = document.metadata.get("chunk_id")
            chunk = self.chunk_by_id.get(chunk_id)
            if chunk is None:
                continue
            results.append({
                **chunk,
                "dense_score": float(distance),
                "dense_rank": rank,
            })
        return results

    def bm25_search(self, query: str, k: int = 20) -> list[dict]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        results = []
        for rank, index in enumerate(order[:k], start=1):
            if scores[index] <= 0:
                continue
            results.append({
                **self.chunks[index],
                "bm25_score": float(scores[index]),
                "bm25_rank": rank,
            })
        return results


_index: RetrievalIndex | None = None
_index_lock = threading.Lock()


def get_retrieval_index(force_rebuild: bool = False) -> RetrievalIndex:
    """懒加载检索索引；首次调用会构建/复用 Chroma 索引。"""
    global _index
    if _index is not None and not force_rebuild:
        return _index

    with _index_lock:
        if _index is not None and not force_rebuild:
            return _index

        success, _ = build_knowledge_base(force_rebuild=force_rebuild)
        if not success:
            raise RuntimeError("知识库不可用，请检查 Ollama 与 chromadb 安装")

        from .knowledge_base import _open_vectorstore  # 延迟导入，便于测试替换

        vectorstore = _open_vectorstore()
        data = vectorstore.get(include=["documents", "metadatas"])
        ids = data.get("ids") or []
        documents = data.get("documents") or []
        metadatas = data.get("metadatas") or []

        chunks: list[dict] = []
        seen: set[str] = set()
        for index, (chunk_id, text, meta) in enumerate(zip(ids, documents, metadatas)):
            meta = meta or {}
            safe_id = meta.get("chunk_id") or chunk_id
            if safe_id in seen:
                safe_id = f"{safe_id}:{index}"
            seen.add(safe_id)
            chunks.append({
                "id": safe_id,
                "text": text or "",
                "law": meta.get("law", ""),
                "short_name": meta.get("short_name", ""),
                "book": meta.get("book", ""),
                "article": meta.get("article", ""),
                "article_num": int(meta.get("article_num", 0) or 0),
                "chapter": meta.get("chapter", ""),
                "source": meta.get("source", ""),
                "version_date": meta.get("version_date", ""),
                "effective_date": meta.get("effective_date", ""),
                "source_url": meta.get("source_url", ""),
                "is_template": bool(meta.get("is_template", False)),
            })

        if not chunks:
            raise RuntimeError("向量库为空，请重建知识库")

        tokenized = [tokenize(chunk["text"]) or ["_empty_"] for chunk in chunks]
        _index = RetrievalIndex(
            chunks=chunks,
            bm25=_build_bm25(tokenized),
            tokenized=tokenized,
            vectorstore=vectorstore,
        )
        return _index


def reset_retrieval_index() -> None:
    global _index
    _index = None


def index_info() -> dict:
    return {
        "collection": COLLECTION_NAME,
        "embedding_model": EMBEDDING_MODEL,
        "ollama_host": OLLAMA_HOST,
        "storage_path": str(CHROMA_DB_DIR),
        "loaded": _index is not None,
        "chunks": _index.size if _index else 0,
    }
