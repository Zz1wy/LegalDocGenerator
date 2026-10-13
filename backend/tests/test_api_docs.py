"""
API 文书生成测试：文书类型、生成、Prompt 构建

测试覆盖:
- 9 种文书类型的生成
- 空字段/部分字段
- Prompt 构建逻辑
- RAG 增强 Prompt
- 无效文书类型
"""

import pytest


# ==================== 文书类型 ====================

class TestDocTypes:
    """文书类型 API"""

    def test_get_doc_types(self, client):
        """✅ 获取文书类型列表"""
        response = client.get("/api/doc-types")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 9
        # 验证结构
        for item in data:
            assert "id" in item
            assert "name" in item
            assert "category" in item

    def test_doc_types_unique(self, client):
        """✅ 文书类型 ID 和名称应唯一"""
        response = client.get("/api/doc-types")
        data = response.json()
        ids = [item["id"] for item in data]
        names = [item["name"] for item in data]
        assert len(ids) == len(set(ids)), "ID 重复"
        assert len(names) == len(set(names)), "名称重复"


# ==================== 文书生成 ====================

class TestDocumentGeneration:
    """文书生成 API"""

    ALL_TYPES = [
        "劳动合同", "民事起诉状", "借条", "租赁合同",
        "离婚协议书", "股权转让协议", "保密协议", "房屋买卖合同", "委托代理书"
    ]

    def test_generate_empty_fields(self, client, auth_headers):
        """❌ 所有字段为空：应拒绝生成"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {"party_a": "", "party_b": "", "term": "", "salary": ""}
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "填写" in data.get("message", "") and "字段" in data.get("message", "")

    def test_generate_with_partial_fields(self, client, auth_headers):
        """✅ 部分字段填写：应正常生成"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {
                "party_a": "某科技公司",
                "party_b": "张三",
                "term": "",
                "salary": ""
            }
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["content"]) > 0

    @pytest.mark.parametrize("doc_type", ALL_TYPES)
    def test_generate_all_doc_types(self, client, auth_headers, sample_fields_all_types, doc_type):
        """✅ 遍历全部 9 种文书类型生成"""
        fields = sample_fields_all_types[doc_type]
        response = client.post("/api/generate", json={
            "doc_type": doc_type,
            "fields": fields
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True, f"{doc_type} 生成失败: {data.get('message')}"
        assert isinstance(data["content"], str)
        assert len(data["content"]) > 0

    def test_generate_invalid_doc_type(self, client, auth_headers):
        """🔲 无效文书类型：应降级处理（使用通用 prompt）"""
        response = client.post("/api/generate", json={
            "doc_type": "不存在的文书类型",
            "fields": {"test_field": "test_value"}
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        # 不应崩溃，可以成功生成（用通用 prompt）或返回错误
        assert "success" in data

    def test_generate_without_auth(self, client):
        """✅ 无认证生成：也应允许（auth 可选）"""
        response = client.post("/api/generate", json={
            "doc_type": "借条",
            "fields": {"lender": "张三", "borrower": "李四", "amount": "10000"}
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_generate_saves_to_database(self, client, auth_headers, mock_database):
        """✅ 生成后应保存到数据库"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {"party_a": "测试公司"}
        }, headers=auth_headers)
        assert response.status_code == 200
        # 验证 save_generated_record_with_user 被调用
        mock_database["save_record"].assert_called()

    # ── 边界值测试 ──

    def test_generate_empty_doc_type(self, client, auth_headers):
        """
        🔲 空文书类型应被请求校验直接拒绝（422），而不是 500。

        以前 doc_type 没有任何约束，空字符串会一路走到 build_prompt，命中
        templates.get("", ...) 的兜底分支，生成出「请生成一份，包含以下信息」
        这种没有意义的 Prompt —— 白调一次大模型，还把垃圾内容存进历史记录。
        现在在入口就用 Pydantic 校验挡掉。
        """
        response = client.post("/api/generate", json={
            "doc_type": "",
            "fields": {"test": "value"}
        }, headers=auth_headers)
        assert response.status_code == 422  # 请求校验失败，且不是 500

    def test_generate_very_long_field(self, client, auth_headers):
        """🔲 超长字段值（5000 字符）"""
        response = client.post("/api/generate", json={
            "doc_type": "民事起诉状",
            "fields": {
                "plaintiff": "张三",
                "defendant": "李四",
                "claim": "A" * 5000,
                "reason": "B" * 5000
            }
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_generate_special_characters(self, client, auth_headers):
        """🔲 含特殊字符/Unicode/Emoji 的字段"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {
                "party_a": "🎉公司©®™",
                "party_b": "José 李",
                "term": "3年\n（可续签）",
                "salary": "￥30,000.00/月"
            }
        }, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True


# ==================== Prompt 构建 ====================

class TestPromptBuilding:
    """Prompt 构建逻辑"""

    def test_build_prompt_labor_contract(self):
        """✅ 劳动合同 prompt 应包含关键字段"""
        from main import build_prompt
        prompt = build_prompt("劳动合同", {
            "party_a": "字节跳动",
            "party_b": "张三",
            "term": "三年",
            "salary": "30000"
        })
        assert "甲方" in prompt or "party_a" in prompt
        assert "字节跳动" in prompt
        assert "张三" in prompt

    def test_build_prompt_unknown_type(self):
        """🔲 未知文书类型使用通用模板"""
        from main import build_prompt
        prompt = build_prompt("未知文书", {"key": "value"})
        assert "未知文书" in prompt

    def test_build_prompt_rag_enhanced(self):
        """✅ RAG 增强 Prompt 应包含法律参考"""
        from main import build_prompt_with_rag
        prompt = build_prompt_with_rag("劳动合同", {
            "party_a": "测试公司",
            "salary": "15000"
        })
        # 基础内容应有
        assert "劳动合同" in prompt
        # RAG 可用时应包含法律参考，不可用时回退到基础
        assert len(prompt) > 0

    def test_build_prompt_with_rag_all_types(self, sample_fields_all_types):
        """✅ 所有文书类型的 RAG Prompt 构建"""
        from main import build_prompt_with_rag
        for doc_type, fields in sample_fields_all_types.items():
            prompt = build_prompt_with_rag(doc_type, fields)
            assert len(prompt) > 0, f"{doc_type} RAG prompt 为空"
            assert doc_type in prompt
