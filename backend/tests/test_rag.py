"""
RAG 模块测试：知识库构建、检索、Embedding

测试覆盖:
- 文档加载
- 文本分块
- 检索查询
- 知识库状态
- 错误处理/回退
"""

import pytest
import sys
import os
from pathlib import Path

# 确保可以导入 rag 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDocumentLoading:
    """文档加载"""

    def test_load_legal_documents(self):
        """✅ 加载法律文档目录"""
        from rag.knowledge_base import load_legal_documents, LEGAL_DATA_DIR

        # 数据目录应存在
        assert LEGAL_DATA_DIR.exists(), f"法律数据目录不存在: {LEGAL_DATA_DIR}"

        docs = load_legal_documents()
        assert len(docs) > 0, "应至少加载一个文档"
        assert all("title" in d and "content" in d and "source" in d for d in docs)

    def test_load_nonexistent_directory(self):
        """🔲 目录不存在时返回空列表"""
        from rag.knowledge_base import load_legal_documents
        docs = load_legal_documents(Path("/nonexistent/path"))
        assert docs == []

    def test_document_content_not_empty(self):
        """✅ 文档内容不应为空"""
        from rag.knowledge_base import load_legal_documents
        docs = load_legal_documents()
        for doc in docs:
            assert len(doc["content"]) > 0, f"{doc['source']} 内容为空"


class TestChunking:
    """文本分块"""

    def test_chunk_documents(self):
        """✅ 文档分块"""
        from rag.knowledge_base import load_legal_documents, chunk_documents
        docs = load_legal_documents()
        chunks = chunk_documents(docs)
        assert len(chunks) > 0
        # 每个块应有 id、text、title 等字段
        for chunk in chunks:
            assert "id" in chunk
            assert "text" in chunk
            assert "title" in chunk
            assert len(chunk["text"]) > 0

    def test_chunk_ids_unique(self):
        """✅ 块 ID 应唯一"""
        from rag.knowledge_base import load_legal_documents, chunk_documents
        docs = load_legal_documents()
        chunks = chunk_documents(docs)
        ids = [c["id"] for c in chunks]
        assert len(ids) == len(set(ids)), "块 ID 重复"

    def test_chunks_not_too_large(self):
        """✅ 每块不超过设定大小（500 + 重叠 80）"""
        from rag.knowledge_base import load_legal_documents, chunk_documents, CHUNK_SIZE, CHUNK_OVERLAP
        docs = load_legal_documents()
        chunks = chunk_documents(docs)
        max_size = CHUNK_SIZE + CHUNK_OVERLAP + 100  # 允许一些余量
        for chunk in chunks:
            assert len(chunk["text"]) <= max_size, \
                f"块 {chunk['id']} 过大: {len(chunk['text'])} > {max_size}"


class TestRetrieval:
    """检索功能"""

    BUILT = False

    @pytest.fixture(autouse=True)
    def ensure_knowledge_base(self):
        """确保知识库已构建（仅构建一次）"""
        if not TestRetrieval.BUILT:
            try:
                from rag.indexer import reset_retrieval_index
                from rag.knowledge_base import build_knowledge_base

                # 只在语料校验和不一致时重建。整库重嵌入很慢，测试不必每次重来。
                build_knowledge_base(force_rebuild=False)
                reset_retrieval_index()
                TestRetrieval.BUILT = True
            except Exception as e:
                pytest.skip(f"知识库构建失败（可能 Ollama 未运行）: {e}")

    def test_retrieve_labor_contract(self):
        """✅ 检索劳动合同相关条文"""
        from rag.retriever import retrieve
        result = retrieve("劳动合同", {
            "party_a": "科技公司",
            "salary": "30000",
            "term": "三年"
        })
        assert "combined" in result
        assert "legal_refs" in result
        assert len(result["combined"]) > 0

    @pytest.mark.parametrize("doc_type,fields", [
        ("劳动合同", {"party_a": "测试公司", "salary": "15000"}),
        ("离婚协议书", {"husband": "张三", "wife": "李四", "child_custody": "女儿归女方"}),
        ("借条", {"lender": "王五", "borrower": "赵六", "amount": "100000"}),
        ("租赁合同", {"landlord": "房东", "tenant": "租客", "rent": "5000"}),
        ("股权转让协议", {"transferor": "卖方", "transferee": "买方", "share_ratio": "50%"}),
        ("保密协议", {"employer": "公司", "employee": "员工"}),
        ("房屋买卖合同", {"seller": "卖方", "buyer": "买方", "house_price": "500万"}),
        ("委托代理书", {"client": "委托人", "agent": "受托人"}),
        ("民事起诉状", {"plaintiff": "原告", "defendant": "被告", "claim": "归还借款"}),
    ])
    def test_retrieve_all_doc_types(self, doc_type, fields):
        """✅ 全部 9 种文书类型的检索"""
        from rag.retriever import retrieve
        result = retrieve(doc_type, fields)
        assert len(result["combined"]) > 0, f"{doc_type} 检索结果为空"

    def test_retrieve_empty_fields(self):
        """🔲 空字段检索"""
        from rag.retriever import retrieve
        result = retrieve("劳动合同", {})
        # 即使字段为空，也应返回结果（使用文书类型名称检索）
        assert len(result["combined"]) > 0

    def test_retrieve_unknown_doc_type(self):
        """🔲 未知文书类型检索"""
        from rag.retriever import retrieve
        result = retrieve("不存在的类型", {"test": "value"})
        # 应优雅降级，返回默认提示
        assert "combined" in result

    def test_retrieve_simple_interface(self):
        """✅ 简化检索接口返回字符串"""
        from rag.retriever import retrieve_simple
        text = retrieve_simple("劳动合同", {"party_a": "测试"})
        assert isinstance(text, str)
        assert len(text) > 0

    def test_retrieve_returns_traceable_citations(self):
        """✅ 检索结果应带法名、条号、正文和来源，供 Prompt 与前端展示"""
        from rag.retriever import retrieve

        result = retrieve("劳动合同", {
            "party_a": "科技公司",
            "salary": "30000",
            "term": "三年"
        })
        assert result["citations"], "混合检索应返回引用对象"
        for citation in result["citations"]:
            assert citation["law"]
            assert citation["article"].startswith("第")
            assert citation["text"]
            assert citation["id"].startswith("S")
        # 在线检索默认走混合检索，而不是纯向量
        assert result["retrieval"]["config"] == "hybrid"

    def test_build_query(self):
        """✅ 查询文本构建"""
        from rag.retriever import build_query
        query = build_query("劳动合同", {
            "party_a": "字节跳动",
            "party_b": "张三",
            "salary": "30000元/月",
            "term": "三年"
        })
        assert "劳动合同" in query
        assert "字节跳动" in query
        assert "30000" in query


class TestKnowledgeBaseStats:
    """知识库统计"""

    def test_get_stats(self):
        """✅ 知识库统计信息"""
        from rag.knowledge_base import get_knowledge_base_stats
        stats = get_knowledge_base_stats()
        assert "status" in stats
        assert "total_chunks" in stats
