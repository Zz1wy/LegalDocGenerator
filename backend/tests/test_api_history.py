"""
历史记录 API 测试：查看、删除、权限控制

测试覆盖:
- 获取历史记录（登录/未登录）
- 删除记录（自己的/他人的）
- 空历史
- 分页和筛选参数
"""

import pytest

from main import create_access_token


class TestHistoryRetrieval:
    """历史记录查询"""

    def test_get_history_authenticated(self, client, auth_headers, mock_database):
        """✅ 已登录用户获取自己的历史"""
        mock_database["history"].return_value = [
            {
                "id": 1, "doc_type": "劳动合同",
                "input_fields": '{"party_a":"测试"}',
                "generated_content": "测试内容",
                "created_at": "2026-06-23 10:00:00"
            }
        ]
        response = client.get("/api/history", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["records"]) == 1

    def test_get_history_unauthenticated(self, client):
        """🔒 未登录用户不能读取任何历史记录（曾经的越权漏洞）"""
        response = client.get("/api/history")
        assert response.status_code == 401

    def test_get_history_with_invalid_token(self, client):
        """🔒 伪造/过期的令牌必须 401，绝不能静默降级成「游客」"""
        response = client.get(
            "/api/history", headers={"Authorization": "Bearer not-a-real-token"}
        )
        assert response.status_code == 401

    def test_get_history_with_token_of_deleted_user(self, client, mock_database):
        """🔒 令牌合法但用户已被删除，也应 401"""
        mock_database["user_by_id"].return_value = None
        token = create_access_token(data={"user_id": 999})
        response = client.get(
            "/api/history", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401

    def test_get_history_empty(self, client, auth_headers, mock_database):
        """🔲 空历史记录"""
        mock_database["history"].return_value = []
        response = client.get("/api/history", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["records"] == []

    def test_get_history_with_limit(self, client, auth_headers, mock_database):
        """✅ 限制返回数量"""
        mock_database["history"].return_value = []
        response = client.get("/api/history?limit=10", headers=auth_headers)
        assert response.status_code == 200


class TestHistoryDeletion:
    """历史记录删除"""

    def test_delete_own_record(self, client, auth_headers, mock_database):
        """✅ 删除自己的记录"""
        mock_database["delete_user"].return_value = True
        response = client.delete("/api/history/1", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_delete_nonexistent_record(self, client, auth_headers, mock_database):
        """❌ 删除不存在的记录"""
        mock_database["delete_user"].return_value = False
        response = client.delete("/api/history/99999", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_delete_unauthenticated(self, client):
        """🔒 未登录用户不能删除任何记录（曾经的越权漏洞）"""
        response = client.delete("/api/history/1")
        assert response.status_code == 401

    def test_delete_others_record_is_rejected(self, client, auth_headers, mock_database):
        """
        🔒 无法删除别人的记录。

        数据库层用 `WHERE id = %s AND user_id = %s` 保证：命中行数为 0 时返回值与
        「记录不存在」完全一致，因此也不泄露「这条记录是否真的存在」。
        """
        mock_database["delete_user"].return_value = False  # 模拟 user_id 不匹配
        response = client.delete("/api/history/1", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["success"] is False

    def test_delete_record_id_zero(self, client, auth_headers):
        """🔲 非法 ID（0）"""
        response = client.delete("/api/history/0", headers=auth_headers)
        assert response.status_code == 200  # 不应 500
        data = response.json()
        assert "success" in data

    def test_delete_record_id_negative(self, client, auth_headers):
        """🔲 非法 ID（负数）"""
        response = client.delete("/api/history/-1", headers=auth_headers)
        assert response.status_code == 200  # 不应 500


class TestHistoryUpdate:
    """在线编辑后保存（PUT /api/history/{id}）"""

    def test_update_own_record(self, client, auth_headers, mock_cursor):
        """✅ 保存自己的文书修改"""
        response = client.put(
            "/api/history/1",
            json={"content": "修改后的文书内容"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["success"] is True
        # 校验归属条件确实写进了 SQL，参数顺序为 (content, record_id, user_id)
        sql, params = mock_cursor.execute.call_args[0]
        assert "user_id = %s" in sql
        assert params == ("修改后的文书内容", 1, 1)

    def test_update_unauthenticated(self, client):
        """🔒 未登录不能修改任何记录"""
        response = client.put("/api/history/1", json={"content": "x"})
        assert response.status_code == 401

    def test_update_others_record_is_rejected(self, client, auth_headers, mock_cursor):
        """🔒 修改别人的记录：命中 0 行，返回失败但不泄露记录是否存在"""
        mock_cursor.rowcount = 0
        response = client.put(
            "/api/history/1",
            json={"content": "越权修改"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["success"] is False

    def test_update_empty_content_rejected(self, client, auth_headers):
        """❌ 空内容应被请求校验拒绝（422），不能把记录清空"""
        response = client.put(
            "/api/history/1", json={"content": ""}, headers=auth_headers
        )
        assert response.status_code == 422

    def test_update_oversized_content_rejected(self, client, auth_headers):
        """❌ 超过 200000 字的内容应被拒绝，避免撑爆数据库"""
        response = client.put(
            "/api/history/1",
            json={"content": "A" * 200_001},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_update_nonexistent_record(self, client, auth_headers, mock_cursor):
        """🔲 不存在的记录：正常返回失败，而不是 500"""
        mock_cursor.rowcount = 0
        response = client.put(
            "/api/history/99999",
            json={"content": "内容"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["success"] is False
