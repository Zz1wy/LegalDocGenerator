"""
RAG 知识库构建模块

功能：
1. 加载 legal_data/ 目录下的法律条文 markdown 文件
2. 用 RecursiveCharacterTextSplitter 将长文本切分为合适大小的块
3. 调用 Ollama nomic-embed-text 模型生成向量
4. 存入 ChromaDB 向量数据库

概念解释：
- Chunk（块）：太长文本 LLM 处理不了，切成小块（约500字/块）
- Embedding（向量化）：把文本转换成数字向量，语义相近的文本向量距离近
- ChromaDB：专门存储和检索向量的轻量数据库
"""

import os
import shutil
from pathlib import Path

# LangChain 组件导入（带优雅降级：缺依赖时 RAG 自动禁用）
# - OllamaEmbeddings: LangChain 官方封装的 Ollama 向量化，替代手写的 httpx 调用
# - Chroma: LangChain 对 ChromaDB 的统一封装，提供 VectorStore 标准接口
try:
    from langchain_ollama import OllamaEmbeddings
    from langchain_community.vectorstores import Chroma
    LANGCHAIN_AVAILABLE = True
except ImportError as e:
    OllamaEmbeddings = None
    Chroma = None
    LANGCHAIN_AVAILABLE = False
    print(f"⚠️ LangChain 组件导入失败: {e}")

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    RecursiveCharacterTextSplitter = None

# ==================== 配置 ====================

# 知识库数据目录
LEGAL_DATA_DIR = Path(__file__).parent / "legal_data"

# ChromaDB 持久化存储目录
CHROMA_DB_DIR = Path(__file__).parent / "chroma_db"

# Ollama 配置
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBEDDING_MODEL = "nomic-embed-text"

# 分块参数
CHUNK_SIZE = 500      # 每块最大字符数
CHUNK_OVERLAP = 80    # 相邻块之间重叠的字符数（避免语义断裂）


# ==================== 知识库构建 ====================
# 说明：原手写的 OllamaEmbeddingFunction 已被 LangChain 的 OllamaEmbeddings 替代。
# OllamaEmbeddings 内部已封装 /api/embeddings 调用、批量处理、错误处理等逻辑，
# 使用方式：embeddings = OllamaEmbeddings(model=..., base_url=...)

def load_legal_documents(data_dir: Path = LEGAL_DATA_DIR) -> list[dict]:
    """
    加载法律文档目录中的所有 markdown 文件

    返回格式: [{"title": "劳动法", "content": "...全文...", "source": "labor_law.md"}, ...]
    """
    documents = []

    if not data_dir.exists():
        print(f"⚠️ 法律数据目录不存在: {data_dir}")
        return documents

    for filepath in data_dir.glob("*.md"):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # 提取第一行作为标题（去掉 # 号）
        lines = content.strip().split("\n")
        title = lines[0].strip("# ").strip() if lines else filepath.stem

        documents.append({
            "title": title,
            "content": content,
            "source": filepath.name
        })
        print(f"  📄 加载: {filepath.name} → {title}")

    return documents


def chunk_documents(documents: list[dict]) -> list[dict]:
    """
    将文档切分为适合检索的小块

    为什么需要切块？
    1. 长文本 Embedding 效果不好
    2. 检索时小块更精准，减少无关信息
    3. LLM 的上下文窗口有限

    重叠机制：每块之间重叠 80 字符，防止关键信息正好卡在分割边界上
    """
    if RecursiveCharacterTextSplitter is None:
        raise ImportError("请安装 langchain-text-splitters: pip install langchain-text-splitters")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],  # 优先在段落/句子边界切分
    )

    chunks = []
    for doc in documents:
        texts = splitter.split_text(doc["content"])
        for i, text in enumerate(texts):
            chunks.append({
                "text": text,
                "title": doc["title"],
                "source": doc["source"],
                "chunk_index": i,
                "id": f"{doc['source']}_{i}"
            })
        print(f"  ✂️  {doc['title']}: {len(texts)} 个块")

    return chunks


def build_knowledge_base(force_rebuild: bool = False) -> tuple[bool, int]:
    """
    构建/重建法律知识库（基于 LangChain Chroma + OllamaEmbeddings）

    流程: 加载文档 → 切块 → 向量化(OllamaEmbeddings) → 存入 Chroma(VectorStore)

    LangChain 封装的优势：
    - OllamaEmbeddings 自动处理批量向量化和错误重试
    - Chroma 提供统一 VectorStore 接口，后续可无缝切换其他向量库（FAISS/Qdrant）
    - 后续可直接 .as_retriever() 获得检索器，无需手写查询逻辑
    """
    if not LANGCHAIN_AVAILABLE:
        print("❌ LangChain 未安装，请运行: pip install langchain langchain-community langchain-ollama")
        return False, 0

    # 检查是否可以跳过
    if not force_rebuild and CHROMA_DB_DIR.exists() and any(CHROMA_DB_DIR.iterdir()):
        print("✅ 知识库已存在，跳过构建（如需重建请设置 force_rebuild=True）")
        try:
            embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_HOST)
            vs = Chroma(
                collection_name="legal_knowledge",
                embedding_function=embeddings,
                persist_directory=str(CHROMA_DB_DIR)
            )
            count = len(vs.get()["ids"])
            return True, count
        except Exception:
            pass

    print("\n🔨 开始构建法律知识库...\n")

    # 1. 加载文档
    print("📂 Step 1/3: 加载法律文档...")
    documents = load_legal_documents()
    if not documents:
        print("❌ 没有找到法律文档")
        return False, 0

    # 2. 切块
    print(f"\n✂️  Step 2/3: 文本切块（{CHUNK_SIZE}字/块，重叠{CHUNK_OVERLAP}字）...")
    chunks = chunk_documents(documents)
    print(f"  总计: {len(chunks)} 个文本块")

    # 3. 用 LangChain Chroma + OllamaEmbeddings 向量化并存储
    print(f"\n🧮 Step 3/3: 向量化并存储（LangChain OllamaEmbeddings + {EMBEDDING_MODEL}）...")

    # 强制重建时删除旧数据目录（LangChain Chroma 没有直接的删集合方法）
    if force_rebuild and CHROMA_DB_DIR.exists():
        shutil.rmtree(CHROMA_DB_DIR, ignore_errors=True)

    # OllamaEmbeddings：封装了对 Ollama /api/embeddings 的调用
    embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_HOST)
    # Chroma VectorStore：传入 embedding_function，add_texts 时自动向量化
    vectorstore = Chroma(
        collection_name="legal_knowledge",
        embedding_function=embeddings,
        persist_directory=str(CHROMA_DB_DIR)
    )

    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [{"title": c["title"], "source": c["source"], "chunk_index": c["chunk_index"]} for c in chunks]

    # 分批存入（LangChain 内部自动调用 Ollama 生成向量）
    batch_size = 10
    for i in range(0, len(chunks), batch_size):
        vectorstore.add_texts(
            texts=texts[i:i + batch_size],
            metadatas=metadatas[i:i + batch_size],
            ids=ids[i:i + batch_size]
        )
        print(f"  💾 已存储 {min(i + batch_size, len(chunks))}/{len(chunks)} 块")

    print(f"\n✅ 知识库构建完成！共 {len(chunks)} 个文本块")
    return True, len(chunks)


def get_knowledge_base_stats() -> dict:
    """获取知识库统计信息（基于 LangChain Chroma 接口）"""
    if not LANGCHAIN_AVAILABLE or not CHROMA_DB_DIR.exists():
        return {"status": "not_built", "total_chunks": 0}

    try:
        embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_HOST)
        vs = Chroma(
            collection_name="legal_knowledge",
            embedding_function=embeddings,
            persist_directory=str(CHROMA_DB_DIR)
        )
        # get() 返回所有文档，ids 列表长度即为总块数
        count = len(vs.get()["ids"])
        return {
            "status": "ready",
            "total_chunks": count,
            "storage_path": str(CHROMA_DB_DIR)
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ==================== 命令行入口 ====================

if __name__ == "__main__":
    # 直接运行此文件即可构建知识库
    success, count = build_knowledge_base(force_rebuild=True)
    if success:
        print(get_knowledge_base_stats())
