"""
RAG 检索模块

功能：
根据用户输入的文书类型和字段信息，从 ChromaDB 中检索最相关的法律条文和模板

检索策略（混合检索）：
1. 用文书类型名称 + 关键字段值构建查询文本
2. 在 ChromaDB 中做语义相似度检索
3. 返回 Top-K 最相关的结果
4. 额外追加：根据文书类型精确匹配的法律条文
"""

import os
from pathlib import Path

try:
    import chromadb
except ImportError:
    chromadb = None

from .knowledge_base import CHROMA_DB_DIR, OllamaEmbeddingFunction, EMBEDDING_MODEL, OLLAMA_HOST

# 检索参数
TOP_K = 5  # 返回最相似的前 K 条


# ==================== 查询文本构建 ====================

def build_query(doc_type: str, fields: dict) -> str:
    """
    将用户输入构建为检索查询文本

    例如：
      输入: doc_type="劳动合同", fields={"party_a": "字节跳动", "salary": "30000"}
      输出: "劳动合同 甲方 字节跳动 工资 30000"
    """
    # 字段名中英文映射（让查询更自然）
    field_labels = {
        "party_a": "甲方",
        "party_b": "乙方",
        "term": "期限",
        "salary": "工资",
        "plaintiff": "原告",
        "defendant": "被告",
        "claim": "诉讼请求",
        "reason": "事实理由",
        "lender": "出借人",
        "borrower": "借款人",
        "amount": "金额",
        "repay_date": "还款日期",
        "landlord": "出租方",
        "tenant": "承租方",
        "property": "租赁物",
        "rent": "租金",
        "husband": "男方",
        "wife": "女方",
        "child_custody": "子女抚养",
        "property_split": "财产分割",
        "transferor": "转让方",
        "transferee": "受让方",
        "company_name": "公司",
        "share_ratio": "股权比例",
        "share_price": "转让价格",
        "employer": "甲方",
        "employee": "乙方",
        "confidential_scope": "保密范围",
        "confidential_period": "保密期限",
        "seller": "卖方",
        "buyer": "买方",
        "house_address": "房屋地址",
        "house_area": "房屋面积",
        "house_price": "成交价格",
        "client": "委托人",
        "agent": "受托人",
        "matter": "代理事项",
        "authority": "代理权限",
        "period": "代理期限",
    }

    parts = [doc_type]

    for key, value in fields.items():
        if value and str(value).strip():
            label = field_labels.get(key, key)
            parts.append(f"{label} {value}")

    query = " ".join(parts)
    return query


# ==================== 精确匹配：文书类型→法律条文映射 ====================

# 不同文书类型对应的最佳法律参考
DOC_TYPE_TO_LEGAL_REF = {
    "劳动合同": "劳动法 劳动合同 工资 试用期 解除合同 经济补偿",
    "民事起诉状": "民事诉讼法 起诉状 诉讼请求 事实与理由 证据",
    "借条": "借款合同 借条 利息 还款期限 违约责任",
    "租赁合同": "租赁合同 出租 承租 租金 租赁期限 维修",
    "离婚协议书": "离婚 子女抚养 财产分割 抚养费 共同债务 婚姻家庭",
    "股权转让协议": "股权转让 股东 优先购买权 出资 变更登记 公司",
    "保密协议": "保密协议 商业秘密 保密义务 违约责任 竞业限制",
    "房屋买卖合同": "房屋买卖 买卖合同 过户 产权 定金 税费",
    "委托代理书": "委托代理 代理权限 代理期限 受托人 委托人",
}


# ==================== 检索接口 ====================

def retrieve(doc_type: str, fields: dict, top_k: int = TOP_K) -> dict:
    """
    主检索函数

    参数:
        doc_type: 文书类型（如"劳动合同"）
        fields:   用户填写的字段字典
        top_k:    返回结果数量

    返回:
        {
            "legal_refs": ["相关法律条文1", "相关法律条文2", ...],
            "template_refs": ["模板片段1", ...],
            "combined": "合并后的参考文本，可以直接拼入 Prompt"
        }
    """
    result = {
        "legal_refs": [],
        "template_refs": [],
        "combined": ""
    }

    # 1. 语义检索（从向量库中找相似内容）
    if chromadb and CHROMA_DB_DIR.exists():
        try:
            query = build_query(doc_type, fields)
            client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
            collection = client.get_collection("legal_knowledge")

            # 生成查询向量
            embed_fn = OllamaEmbeddingFunction()
            query_embedding = embed_fn([query])[0]

            # 向量检索
            search_results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k
            )

            # 提取检索到的文本
            if search_results and search_results.get("documents"):
                for doc_list in search_results["documents"]:
                    for doc_text in doc_list:
                        if doc_text and doc_text not in result["legal_refs"]:
                            result["legal_refs"].append(doc_text)

        except Exception as e:
            print(f"⚠️ 向量检索失败: {e}")
            # 向量检索失败回退到关键词匹配
            search_results = None
    else:
        search_results = None

    # 2. 精确匹配：根据文书类型追加相关法律提示
    doc_keywords = DOC_TYPE_TO_LEGAL_REF.get(doc_type, doc_type)
    if not result["legal_refs"]:
        result["legal_refs"].append(f"请参考中国法律法规中关于{doc_keywords}的相关规定")

    # 3. 合并为 Prompt 可用的格式
    parts = []
    if result["legal_refs"]:
        parts.append("【相关法律参考】")
        for i, ref in enumerate(result["legal_refs"], 1):
            parts.append(f"{i}. {ref}")

    result["combined"] = "\n\n".join(parts)
    return result


def retrieve_simple(doc_type: str, fields: dict, top_k: int = TOP_K) -> str:
    """
    简化版检索接口：直接返回合并后的文本

    用于在 build_prompt 中无缝集成
    """
    result = retrieve(doc_type, fields, top_k)
    return result["combined"]


# ==================== 测试 ====================

if __name__ == "__main__":
    # 测试检索
    test_cases = [
        ("劳动合同", {"party_a": "某科技公司", "salary": "15000元/月", "term": "三年"}),
        ("离婚协议书", {"husband": "张三", "wife": "李四", "child_custody": "女儿归女方", "property_split": "房产归男方"}),
        ("借条", {"lender": "王五", "borrower": "赵六", "amount": "10万元", "repay_date": "2025年12月31日"}),
    ]

    for doc_type, fields in test_cases:
        print(f"\n{'='*60}")
        print(f"📝 文书类型: {doc_type}")
        print(f"📋 字段: {fields}")
        print(f"{'='*60}")

        result = retrieve(doc_type, fields)
        print(f"\n检索到 {len(result['legal_refs'])} 条相关法律参考")
        print(result["combined"][:500])
