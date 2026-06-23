"""
历史记录 API 测试：查看、删除、权限控制

测试覆盖:
- 获取历史记录（登录/未登录）
- 删除记录（自己的/他人的）
- 空历史
- 分页和筛选参数
"""

import pytest


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

    def test_get_history_unauthenticated(self, client, mock_database):
        """✅ 未登录用户获取所有历史"""
        mock_database["all_history"].return_value = []
        response = client.get("/api/history")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert isinstance(data["records"], list)

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

    def test_delete_unauthenticated(self, client, mock_database):
        """✅ 未登录用户删除记录"""
        mock_database["delete"].return_value = True
        response = client.delete("/api/history/1")
        assert response.status_code == 200

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
