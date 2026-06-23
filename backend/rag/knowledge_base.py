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
import json
import httpx
from pathlib import Path

# ChromaDB 和 LangChain 的导入
try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    chromadb = None

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


# ==================== Ollama Embedding 封装 ====================

class OllamaEmbeddingFunction:
    """
    自定义 Embedding 函数，调用 Ollama 的 /api/embeddings 接口

    为什么自定义？ChromaDB 内置的 embedding 函数通常调用 OpenAI API，
    但我们的 Ollama 是本地免费的，所以需要自己封装。
    """

    def __init__(self, host: str = OLLAMA_HOST, model: str = EMBEDDING_MODEL):
        self.host = host
        self.model = model

    def __call__(self, texts: list[str]) -> list[list[float]]:
        """接收文本列表，返回向量列表"""
        embeddings = []
        for text in texts:
            try:
                # 同步调用 Ollama
                import httpx
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(
                        f"{self.host}/api/embeddings",
                        json={"model": self.model, "prompt": text}
                    )
                    if response.status_code == 200:
                        embedding = response.json().get("embedding", [])
                        embeddings.append(embedding)
                    else:
                        # 失败时返回零向量（实际生产应做更好的错误处理）
                        print(f"  ⚠️ Embedding 失败: {response.status_code}")
                        embeddings.append([0.0] * 768)
            except Exception as e:
                print(f"  ⚠️ Embedding 异常: {e}")
                embeddings.append([0.0] * 768)
        return embeddings


# ==================== 知识库构建 ====================

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
    构建/重建法律知识库

    流程: 加载文档 → 切块 → 向量化 → 存入 ChromaDB

    参数:
        force_rebuild: 是否强制重建（即使已有数据）

    返回: (是否成功, 总块数)
    """
    if chromadb is None:
        print("❌ ChromaDB 未安装，请运行: pip install chromadb")
        return False, 0

    # 检查是否可以跳过
    if not force_rebuild and CHROMA_DB_DIR.exists() and any(CHROMA_DB_DIR.iterdir()):
        print("✅ 知识库已存在，跳过构建（如需重建请设置 force_rebuild=True）")
        # 快速统计已有数据
        client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
        try:
            collection = client.get_collection("legal_knowledge")
            count = collection.count()
            return True, count
        except:
            pass

    print("\n🔨 开始构建法律知识库...\n")

    # 1. 加载文档
    print("📂 Step 1/4: 加载法律文档...")
    documents = load_legal_documents()
    if not documents:
        print("❌ 没有找到法律文档")
        return False, 0

    # 2. 切块
    print(f"\n✂️  Step 2/4: 文本切块（{CHUNK_SIZE}字/块，重叠{CHUNK_OVERLAP}字）...")
    chunks = chunk_documents(documents)
    print(f"  总计: {len(chunks)} 个文本块")

    # 3. 初始化 ChromaDB
    print("\n🗄️  Step 3/4: 初始化向量数据库...")
    client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))

    # 如果已存在同名 collection，先删除再重建
    try:
        client.delete_collection("legal_knowledge")
    except:
        pass

    collection = client.create_collection(
        name="legal_knowledge",
        metadata={"description": "中国法律法规知识库"}
    )

    # 4. 向量化并存入
    print(f"\n🧮 Step 4/4: 向量化并存储（使用 {EMBEDDING_MODEL}）...")
    embed_fn = OllamaEmbeddingFunction()

    # 分批处理（避免一次发送太多文本）
    batch_size = 10
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        texts = [c["text"] for c in batch]
        ids = [c["id"] for c in batch]
        metadatas = [{"title": c["title"], "source": c["source"], "chunk_index": c["chunk_index"]} for c in batch]

        # 生成向量
        embeddings = embed_fn(texts)

        # 存入 ChromaDB
        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas
        )
        print(f"  💾 已存储 {min(i + batch_size, len(chunks))}/{len(chunks)} 块")

    print(f"\n✅ 知识库构建完成！共 {len(chunks)} 个文本块")
    return True, len(chunks)


def get_knowledge_base_stats() -> dict:
    """获取知识库统计信息"""
    if chromadb is None or not CHROMA_DB_DIR.exists():
        return {"status": "not_built", "total_chunks": 0}

    try:
        client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
        collection = client.get_collection("legal_knowledge")
        return {
            "status": "ready",
            "total_chunks": collection.count(),
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
