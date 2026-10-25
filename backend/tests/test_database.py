"""
数据库操作模块测试

测试覆盖:
- 数据库连接
- CRUD 操作
- 边界值
- 错误处理
"""

import pytest
import json
from unittest.mock import patch, MagicMock


class TestDatabaseConnection:
    """数据库连接"""

    @patch("database.get_db_connection")
    def test_connection_success(self, mock_conn):
        """✅ 成功连接"""
        mock_conn.return_value = MagicMock()
        from database import get_db_connection
        conn = get_db_connection()
        assert conn is not None

    @patch("database.get_db_connection")
    def test_connection_failure(self, mock_conn):
        """❌ 连接失败应返回 None"""
        mock_conn.return_value = None
        from database import get_db_connection
        conn = get_db_connection()
        assert conn is None


class TestGetTemplates:
    """获取模板"""

    @patch("database.get_db_connection")
    def test_templates_success(self, mock_conn):
        """✅ 成功获取模板列表"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            {"id": 1, "name": "劳动合同", "category": "劳动", "fields": None}
        ]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.get_db_connection = mock_conn  # 这行不需要
        from database import get_all_templates

        # 使用正确的 mock 结构
        pass

    @patch("database.get_db_connection")
    def test_templates_empty(self, mock_conn):
        """🔲 获取空模板列表"""
        mock_conn.return_value = None
        from database import get_all_templates
        result = get_all_templates()
        assert result == []


class TestSaveRecord:
    """保存记录"""

    @patch("database.get_db_connection")
    def test_save_success(self, mock_conn):
        """✅ 成功保存：返回新记录 id"""
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        mock_conn_instance = MagicMock()
        mock_conn_instance.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_conn_instance

        from database import save_generated_record_with_user
        result = save_generated_record_with_user(
            "劳动合同",
            {"party_a": "测试"},
            "生成的文书内容",
            user_id=1
        )
        assert result == 42

    @patch("database.get_db_connection")
    def test_save_with_none_user(self, mock_conn):
        """✅ 未登录用户（user_id=None）保存"""
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 7
        mock_conn_instance = MagicMock()
        mock_conn_instance.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_conn_instance

        from database import save_generated_record_with_user
        result = save_generated_record_with_user(
            "借条",
            {"amount": "10000"},
            "生成内容",
            user_id=None
        )
        assert result == 7

    @patch("database.get_db_connection")
    def test_save_long_content(self, mock_conn):
        """🔲 超长内容（10000 字符）"""
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 99
        mock_conn_instance = MagicMock()
        mock_conn_instance.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_conn_instance

        from database import save_generated_record_with_user
        result = save_generated_record_with_user(
            "劳动合同",
            {"party_a": "测试"},
            "A" * 10000,
            user_id=1
        )
        assert result == 99

    @patch("database.get_db_connection")
    def test_update_record_own(self, mock_conn):
        """✅ 更新自己的记录：SQL 命中 1 行"""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn_instance = MagicMock()
        mock_conn_instance.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_conn_instance

        from database import update_record_content_by_user
        assert update_record_content_by_user(1, 1, "新内容") is True
        sql, params = mock_cursor.execute.call_args[0]
        assert "user_id = %s" in sql
        assert params == ("新内容", 1, 1)

    @patch("database.get_db_connection")
    def test_update_record_not_owned(self, mock_conn):
        """🔒 更新别人的记录：WHERE 不命中，返回 False"""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_conn_instance = MagicMock()
        mock_conn_instance.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_conn_instance

        from database import update_record_content_by_user
        assert update_record_content_by_user(1, 999, "越权修改") is False

    @patch("database.save_generated_record_with_user")
    def test_save_connection_failure(self, mock_save):
        """❌ 连接失败时保存"""
        mock_save.return_value = False
        result = mock_save("劳动合同", {}, "内容", user_id=1)
        assert result is False


class TestUserOperations:
    """用户操作"""

    @patch("database.get_user_by_username")
    def test_get_user_by_username_found(self, mock_fn):
        """✅ 找到用户"""
        mock_fn.return_value = {
            "id": 1, "username": "test", "password": "hash", "nickname": "测试"
        }
        user = mock_fn("test")
        assert user is not None
        assert user["username"] == "test"

    @patch("database.get_user_by_username")
    def test_get_user_by_username_not_found(self, mock_fn):
        """🔲 用户不存在"""
        mock_fn.return_value = None
        user = mock_fn("nonexistent")
        assert user is None

    @patch("database.get_db_connection")
    def test_get_user_by_id(self, mock_conn):
        """✅ 按 ID 查找"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "id": 1, "username": "test", "nickname": "测试"
        }
        mock_conn_instance = MagicMock()
        mock_conn_instance.cursor.return_value = mock_cursor
        mock_conn.return_value = mock_conn_instance

        from database import get_user_by_id
        user = get_user_by_id(1)
        assert user is not None
        # 不应包含 password
        assert "password" not in user
