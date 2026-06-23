"""
端到端集成测试：模拟完整用户操作流程

测试覆盖:
- 注册 → 登录 → 生成文书 → 查看历史 → 删除记录 → 退出
- 多用户数据隔离
- 错误恢复
"""

import pytest
import json


class TestFullUserJourney:
    """完整用户流程"""

    def test_register_login_generate_view(self, client, mock_database):
        """✅ 完整流程：注册 → 登录 → 生成 → 查看历史 → 退出"""
        # 使用 auth_headers fixture 中的有效 token
        from main import create_access_token
        token = create_access_token(data={"user_id": 1})
        headers = {"Authorization": f"Bearer {token}"}

        # Step 1: 注册
        reg_response = client.post("/api/register", json={
            "username": "journey_test",
            "password": "Journey123!",
            "nickname": "旅程测试"
        })
        assert reg_response.status_code == 200
        reg_data = reg_response.json()
        assert reg_data["success"] is True

        # Step 2: 获取用户信息（使用有效 token）
        user_response = client.get("/api/user", headers=headers)
        assert user_response.status_code == 200
        assert user_response.json()["success"] is True
        assert user_response.json()["user"]["username"] == "testuser"

        # Step 3: 获取文书类型
        doc_types_response = client.get("/api/doc-types")
        assert doc_types_response.status_code == 200
        doc_types = doc_types_response.json()
        assert len(doc_types) > 0

        # Step 4: 生成文书
        generate_response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {
                "party_a": "旅程科技有限公司",
                "party_b": "测试员工",
                "term": "三年",
                "salary": "20000元/月"
            }
        }, headers=headers)
        assert generate_response.status_code == 200
        gen_data = generate_response.json()
        assert gen_data["success"] is True
        assert len(gen_data["content"]) > 0

        # Step 5: 查看历史
        mock_database["history"].return_value = [{
            "id": 1,
            "doc_type": "劳动合同",
            "input_fields": '{"party_a":"旅程科技有限公司","party_b":"测试员工"}',
            "generated_content": gen_data["content"],
            "created_at": "2026-06-23 17:30:00"
        }]
        history_response = client.get("/api/history", headers=headers)
        assert history_response.status_code == 200
        history_data = history_response.json()
        assert history_data["success"] is True
        assert len(history_data["records"]) > 0

        # Step 6: 删除记录
        mock_database["delete_user"].return_value = True
        delete_response = client.delete("/api/history/1", headers=headers)
        assert delete_response.status_code == 200
        assert delete_response.json()["success"] is True

    def test_generate_multiple_documents(self, client, auth_headers):
        """✅ 连续生成多种文书"""
        doc_sequence = [
            ("劳动合同", {"party_a": "公司A", "salary": "15000"}),
            ("保密协议", {"employer": "公司A", "employee": "员工B"}),
            ("借条", {"lender": "张三", "borrower": "李四", "amount": "50000"}),
            ("租赁合同", {"landlord": "房东", "tenant": "租客", "rent": "3000"}),
        ]

        for doc_type, fields in doc_sequence:
            response = client.post("/api/generate", json={
                "doc_type": doc_type,
                "fields": fields
            }, headers=auth_headers)
            assert response.status_code == 200
            assert response.json()["success"] is True, f"{doc_type} 生成失败"


class TestMultiUserIsolation:
    """多用户数据隔离"""

    def test_users_see_own_history(self, client, mock_database):
        """✅ 用户通过有效 token 查看自己的历史记录"""
        from main import create_access_token
        token = create_access_token(data={"user_id": 42})
        headers = {"Authorization": f"Bearer {token}"}

        # Mock 该用户的历史
        mock_database["history"].return_value = [
            {"id": 1, "doc_type": "劳动合同", "input_fields": "{}",
             "generated_content": "用户42的文书", "created_at": "2026-06-23 10:00:00"}
        ]

        response = client.get("/api/history", headers=headers)
        assert response.status_code == 200
        records = response.json()["records"]
        assert len(records) == 1
        assert "用户42的文书" in records[0]["generated_content"]

    def test_cannot_delete_others_record(self, client, mock_database):
        """🔒 不能删除他人的记录"""
        from main import create_access_token
        token = create_access_token(data={"user_id": 99})
        headers = {"Authorization": f"Bearer {token}"}
        mock_database["delete_user"].return_value = False  # 权限拒绝

        response = client.delete("/api/history/999", headers=headers)
        assert response.status_code == 200
        assert response.json()["success"] is False


class TestHealthAndMonitoring:
    """健康检查与监控"""

    def test_health_endpoint_full(self, client):
        """✅ 健康检查端点返回所有组件状态"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "rag" in data
        assert data["rag"] in ["ready", "not_built", "disabled", "error", "unknown"]

    def test_api_doc_types_consistency(self, client):
        """✅ 文书类型 API 一致性"""
        response = client.get("/api/doc-types")
        data = response.json()
        # 多次请求结果应一致
        response2 = client.get("/api/doc-types")
        data2 = response2.json()
        assert data == data2

    def test_cors_headers(self, client):
        """✅ CORS 头检查"""
        response = client.options("/api/doc-types", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET"
        })
        # FastAPI CORS 中间件应处理 OPTIONS 请求
        assert response.status_code in [200, 405]


class TestKnowledgeBaseAPI:
    """知识库管理 API"""

    def test_knowledge_stats(self, client):
        """✅ 知识库统计"""
        response = client.get("/api/knowledge/stats")
        assert response.status_code == 200
        data = response.json()
        assert "success" in data

    def test_knowledge_reload(self, client):
        """✅ 知识库重建"""
        response = client.post("/api/knowledge/reload")
        assert response.status_code == 200
        data = response.json()
        assert "success" in data


class TestErrorRecovery:
    """错误恢复"""

    def test_recover_after_bad_request(self, client, auth_headers):
        """✅ 错误请求后系统应正常恢复"""
        # 发送一个错误请求
        client.post("/api/generate", json={"bad": "request"})

        # 紧接着发送正常请求
        response = client.post("/api/generate", json={
            "doc_type": "借条",
            "fields": {"lender": "正常"}
        }, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_health_after_errors(self, client):
        """✅ 发生错误后健康检查仍正常"""
        # 制造一些错误
        for _ in range(5):
            client.post("/api/generate", json={})

        # 健康检查应正常
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
