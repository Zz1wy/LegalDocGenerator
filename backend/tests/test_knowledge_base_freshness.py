"""索引新鲜度检查：元数据存在不代表向量库真的有数据。"""

import chromadb

import rag.knowledge_base as kb


def test_index_meta_lives_with_vector_store():
    """Docker 里只有 chroma_db 是持久化卷，元数据必须跟它在一起。"""
    assert kb.INDEX_META_PATH.parent == kb.CHROMA_DB_DIR


def test_stored_chunk_count_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(kb, "CHROMA_DB_DIR", tmp_path / "missing")
    assert kb._stored_chunk_count() == 0


def test_stored_chunk_count_reads_collection(tmp_path, monkeypatch):
    db_dir = tmp_path / "chroma"
    db_dir.mkdir()
    monkeypatch.setattr(kb, "CHROMA_DB_DIR", db_dir)

    client = chromadb.PersistentClient(path=str(db_dir))
    collection = client.get_or_create_collection(kb.COLLECTION_NAME)
    collection.add(
        ids=["a", "b"],
        embeddings=[[0.1, 0.2], [0.3, 0.4]],
        documents=["a", "b"],
    )

    assert kb._stored_chunk_count() == 2


def test_empty_collection_is_not_a_fresh_index(tmp_path, monkeypatch):
    """空数据卷 + 旧元数据时，count 必须为 0，从而触发重建。"""
    db_dir = tmp_path / "chroma"
    db_dir.mkdir()
    monkeypatch.setattr(kb, "CHROMA_DB_DIR", db_dir)

    client = chromadb.PersistentClient(path=str(db_dir))
    client.get_or_create_collection(kb.COLLECTION_NAME)

    assert kb._stored_chunk_count() == 0
