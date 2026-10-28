"""
RAG 知识库构建模块（法条级切块版）。

与旧版的区别：
- 旧版：整部法律按 500 字硬切，块里没有法名/条号，检索到了也无法引用；
- 新版：以「第X条」为最小单元，保留 law/article/book/version/source_url 元数据。

流程：加载语料 → 解析法条 → 切块 → OllamaEmbeddings 向量化 → Chroma 持久化
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .corpus import (
    LEGAL_DATA_DIR,
    chinese_to_int,
    extract_articles,
    load_legal_documents as _load_legal_documents,
)

try:
    from langchain_ollama import OllamaEmbeddings
    from langchain_community.vectorstores import Chroma
    LANGCHAIN_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - 依赖缺失时的降级路径
    OllamaEmbeddings = None
    Chroma = None
    LANGCHAIN_AVAILABLE = False
    print(f"⚠️ LangChain 组件导入失败: {exc}")

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:  # pragma: no cover
    RecursiveCharacterTextSplitter = None

# ==================== 配置 ====================

CHROMA_DB_DIR = Path(os.getenv("CHROMA_DB_DIR", str(Path(__file__).parent / "chroma_db")))
# 元数据必须跟向量库放在同一个目录：Docker 里 chroma_db 是持久化卷，
# 而代码目录是镜像层。若元数据留在代码目录，重建容器后会出现
# 「元数据存在、向量库为空」的假命中，启动时跳过重建。
INDEX_META_PATH = CHROMA_DB_DIR / "index_meta.json"
COLLECTION_NAME = "legal_knowledge"
# v3：修复章节边界处的 chapter 元数据错位（每章最后一条被记到下一章）
INDEX_VERSION = 3

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
MAX_CHUNK_SIZE = CHUNK_SIZE + CHUNK_OVERLAP + 50  # 550；测试允许到 680

BATCH_SIZE = 32


def load_legal_documents(data_dir: Path = LEGAL_DATA_DIR) -> list[dict]:
    """兼容旧接口：加载目录下所有 .md 法律文件。"""
    return _load_legal_documents(data_dir)


# ==================== 切块 ====================


def _splitter() -> "RecursiveCharacterTextSplitter":
    if RecursiveCharacterTextSplitter is None:
        raise ImportError(
            "请安装 langchain-text-splitters: pip install langchain-text-splitters"
        )
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    )


def _hard_split(text: str) -> list[str]:
    """极端长句兜底：确保单个块不超过 MAX_CHUNK_SIZE。"""
    if len(text) <= MAX_CHUNK_SIZE:
        return [text]
    return [text[i:i + MAX_CHUNK_SIZE] for i in range(0, len(text), MAX_CHUNK_SIZE)]


def _make_chunk(index: int, document: dict, text: str, article: dict | None) -> dict:
    article_text = article["article"] if article else ""
    return {
        "id": f"{Path(document['source']).stem}:{article_text or 'tpl'}:{index}",
        "text": text,
        "title": document["law"],
        "source": document["source"],
        "chunk_index": index,
        # ---- 引用溯源所需元数据 ----
        "law": document["law"],
        "short_name": document["short_name"],
        "book": document["book"],
        "article": article_text,
        "article_num": article["article_num"] if article else 0,
        "chapter": article["chapter"] if article else "",
        "version_date": document["version_date"],
        "effective_date": document["effective_date"],
        "source_url": document["source_url"],
        "is_template": bool(document["is_template"]),
    }


def chunk_documents(documents: list[dict]) -> list[dict]:
    """把法律文档切成以「条」为单位的检索块。

    - 有条号：一条一块；超长条文再按句子切分，但保留同一条号元数据；
    - 无条号（模板/说明）：退回普通递归切块；
    - 每个块都带 `law/article/book/source_url`，供引用展示与校验。
    """
    if RecursiveCharacterTextSplitter is None:
        raise ImportError(
            "请安装 langchain-text-splitters: pip install langchain-text-splitters"
        )

    splitter = _splitter()
    chunks: list[dict] = []
    global_index = 0

    for document in documents:
        articles = [] if document.get("is_template") else extract_articles(document["content"])
        if not articles:
            # 模板或没有条号结构的文本
            parts = splitter.split_text(document["content"])
            for part in parts:
                for piece in _hard_split(part):
                    chunks.append(_make_chunk(global_index, document, piece, None))
                    global_index += 1
            print(f"  ✂️  {document['source']}: {len(parts)} 个模板/普通块")
            continue

        article_chunks = 0
        for article in articles:
            body = article["text"].strip()
            if not body:
                continue
            parts = splitter.split_text(body) if len(body) > CHUNK_SIZE else [body]
            for part_index, part in enumerate(parts):
                text = part.strip()
                # 后续分段补上条号，避免「第二段」脱离上下文
                if part_index > 0 and not text.lstrip().startswith(article["article"]):
                    prefix = f"《{document['short_name']}》{article['article']}"
                    text = f"{prefix}\n{text}"
                for piece in _hard_split(text):
                    chunks.append(_make_chunk(global_index, document, piece, article))
                    global_index += 1
                    article_chunks += 1
        print(f"  ✂️  {document['source']}: {len(articles)} 条 → {article_chunks} 个块")

    return chunks


# ==================== 索引构建 ====================


def _corpus_checksum(documents: list[dict]) -> str:
    """语料内容 + 切块参数 + embedding 模型的指纹。

    任一变化都应触发重建；只比较文件 mtime 在 CI/容器里不可靠。
    """
    digest = hashlib.sha256()
    digest.update(f"v{INDEX_VERSION}|{CHUNK_SIZE}|{CHUNK_OVERLAP}|{EMBEDDING_MODEL}".encode())
    for document in sorted(documents, key=lambda d: d["source"]):
        digest.update(document["source"].encode("utf-8"))
        digest.update(document["content"].encode("utf-8"))
    return digest.hexdigest()


def _read_index_meta() -> dict:
    if not INDEX_META_PATH.exists():
        return {}
    try:
        return json.loads(INDEX_META_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_index_meta(meta: dict) -> None:
    INDEX_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_META_PATH.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _stored_chunk_count() -> int:
    """实际写入 Chroma 的向量数；读不到时返回 0。

    只检查 INDEX_META_PATH 是否存在不够：Docker 首次挂载空数据卷、
    卷被误删、或镜像里带了旧元数据时，都会出现「元数据在、向量库空」
    的情况，此时必须重建，而不是跳过。
    """
    if not CHROMA_DB_DIR.exists():
        return 0
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
        return client.get_collection(COLLECTION_NAME).count()
    except Exception:
        return 0


def _chroma_metadata(chunk: dict) -> dict:
    """Chroma 只接受 str/int/float/bool，不能有 None。"""
    return {
        "chunk_id": chunk["id"],
        "law": chunk["law"],
        "short_name": chunk["short_name"],
        "book": chunk["book"],
        "article": chunk["article"],
        "article_num": int(chunk["article_num"]),
        "chapter": chunk["chapter"],
        "source": chunk["source"],
        "version_date": chunk["version_date"],
        "effective_date": chunk["effective_date"],
        "source_url": chunk["source_url"],
        "is_template": bool(chunk["is_template"]),
    }


def _open_vectorstore() -> "Chroma":
    embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_HOST)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DB_DIR),
    )


def build_knowledge_base(force_rebuild: bool = False) -> tuple[bool, int]:
    """构建/复用知识库，返回 (是否成功, 文本块数)。

    复用条件：语料内容、切块参数、embedding 模型都没变。
    """
    if not LANGCHAIN_AVAILABLE:
        print("❌ LangChain 未安装，请运行: pip install langchain langchain-community langchain-ollama")
        return False, 0

    documents = load_legal_documents()
    if not documents:
        print("❌ 没有找到法律文档")
        return False, 0

    chunks = chunk_documents(documents)
    if not chunks:
        print("❌ 切块结果为空")
        return False, 0

    checksum = _corpus_checksum(documents)
    meta = _read_index_meta()
    if (
        not force_rebuild
        and meta.get("checksum") == checksum
        and meta.get("count") == len(chunks)
        and _stored_chunk_count() == len(chunks)
    ):
        print(f"✅ 知识库已是最新，共 {len(chunks)} 个块（跳过重建）")
        return True, len(chunks)

    print("\n🔨 构建法律知识库（法条级切块）...")
    if CHROMA_DB_DIR.exists():
        shutil.rmtree(CHROMA_DB_DIR, ignore_errors=True)
    CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)

    try:
        vectorstore = _open_vectorstore()
        # 目录被清空后一般不会有旧集合；若存在则删掉，确保 id 不重复
        try:
            vectorstore.delete_collection()
        except Exception:
            pass
        vectorstore = _open_vectorstore()

        texts = [chunk["text"] for chunk in chunks]
        ids = [chunk["id"] for chunk in chunks]
        metadatas = [_chroma_metadata(chunk) for chunk in chunks]

        for start in range(0, len(chunks), BATCH_SIZE):
            end = start + BATCH_SIZE
            vectorstore.add_texts(
                texts=texts[start:end],
                metadatas=metadatas[start:end],
                ids=ids[start:end],
            )
            print(f"  💾 已存储 {min(end, len(chunks))}/{len(chunks)} 块")
    except Exception as exc:
        print(f"❌ 知识库构建失败: {exc}")
        return False, 0

    _write_index_meta({
        "version": INDEX_VERSION,
        "checksum": checksum,
        "count": len(chunks),
        "embedding_model": EMBEDDING_MODEL,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "laws": sorted({chunk["law"] for chunk in chunks}),
        "corpus_files": [document["source"] for document in documents],
    })

    print(f"\n✅ 知识库构建完成：{len(documents)} 部法规 / {len(chunks)} 个法条块")
    return True, len(chunks)


def get_knowledge_base_stats() -> dict:
    """读取索引元数据；不实例化 embedding，避免统计接口依赖 Ollama。"""
    meta = _read_index_meta()
    if not meta or not CHROMA_DB_DIR.exists():
        return {"status": "not_built", "total_chunks": 0}
    return {
        "status": "ready",
        "total_chunks": meta.get("count", 0),
        "storage_path": str(CHROMA_DB_DIR),
        "embedding_model": meta.get("embedding_model", EMBEDDING_MODEL),
        "chunk_size": meta.get("chunk_size", CHUNK_SIZE),
        "built_at": meta.get("built_at", ""),
        "laws": meta.get("laws", []),
        "corpus_files": meta.get("corpus_files", []),
        "checksum": meta.get("checksum", "")[:12],
    }


if __name__ == "__main__":
    success, count = build_knowledge_base(force_rebuild=True)
    if success:
        print(get_knowledge_base_stats())
