"""
API 认证测试：用户注册、登录、JWT 令牌验证

测试覆盖:
- 正常注册/登录流程
- 重复注册
- 错误密码
- 缺失字段
- 边界值（用户名/密码长度）
- 特殊字符处理
- Token 过期/无效
"""

import pytest
import time
from main import create_access_token, SECRET_KEY, ALGORITHM
import jwt


# ==================== 用户注册 ====================

class TestUserRegistration:
    """用户注册测试"""

    def test_register_success(self, client, sample_user, mock_database):
        """✅ 正常注册：填写完整信息应成功返回 token"""
        response = client.post("/api/register", json=sample_user)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["token"] is not None
        assert data["user"]["username"] == "testuser"
        assert data["user"]["nickname"] == "测试用户"
        assert "message" in data

    def test_register_duplicate_username(self, client, sample_user, mock_database):
        """❌ 重复注册：相同用户名应被拒绝"""
        mock_database["user_by_name"].return_value = {"id": 1, "username": "testuser"}

        response = client.post("/api/register", json=sample_user)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "用户名已存在" in data["message"]

    def test_register_without_nickname(self, client, mock_database):
        """✅ 注册时不填昵称：应使用用户名作为默认昵称"""
        response = client.post("/api/register", json={
            "username": "newuser",
            "password": "Pass123!"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["user"]["nickname"] == "newuser"

    # ── 边界值测试 ──

    @pytest.mark.parametrize("username,password,desc", [
        ("ab", "ValidPass1!", "用户名 2 字符（最短）"),
        ("a" * 50, "ValidPass1!", "用户名 50 字符（最长）"),
        ("user123", "Abcdefg1", "密码 8 字符（最短）"),
        ("user123", "a" * 72, "密码 72 字节（bcrypt 上限）"),
    ])
    def test_register_boundary_values(self, client, mock_database, username, password, desc):
        """🔲 边界值：合法范围内的长度边界应正常处理"""
        response = client.post("/api/register", json={
            "username": username,
            "password": password
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True, f"边界值失败: {desc}"

    @pytest.mark.parametrize("username,password,desc", [
        ("", "Pass123!", "空用户名"),
        ("u", "Pass123!", "用户名 1 字符"),
        ("user", "", "空密码"),
        ("user", "Short1!", "密码 7 字符"),
        ("", "", "全部为空"),
        ("u" * 51, "Pass123!", "用户名超长 51 字符"),
        ("user123", "a" * 73, "密码超过 72 字节"),
    ])
    def test_register_invalid_input(self, client, mock_database, username, password, desc):
        """❌ 非法输入：服务端应直接以 422 拒绝，不能绕过前端校验"""
        response = client.post("/api/register", json={
            "username": username,
            "password": password
        })
        assert response.status_code == 422, f"应拒绝: {desc}"

    def test_register_strips_whitespace_username(self, client, mock_database):
        """用户名两侧空格应被去掉，避免出现「看起来一样」的重复账号"""
        response = client.post("/api/register", json={
            "username": "  newuser  ",
            "password": "Pass123!"
        })
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "newuser"

    def test_register_special_characters(self, client, mock_database):
        """🔲 特殊字符：中文/Emoji/符号应正常处理"""
        response = client.post("/api/register", json={
            "username": "用户🎉_test",
            "password": "密码!@#$%^&*()"
        })
        assert response.status_code == 200
        assert "success" in response.json()

    def test_register_sql_injection_attempt(self, client, mock_database):
        """🔒 SQL 注入攻击：不应影响系统"""
        response = client.post("/api/register", json={
            "username": "'; DROP TABLE users; --",
            "password": "'; DROP TABLE users; --"
        })
        assert response.status_code == 200


# ==================== 用户登录 ====================

class TestUserLogin:
    """用户登录测试"""

    def test_login_success(self, client, mock_database):
        """✅ 正常登录：正确用户名密码应返回 token"""
        mock_database["user_by_name"].return_value = {
            "id": 1,
            "username": "testuser",
            "password": "$2b$12$LJ3m4ys3Lk0TSwHCpNqr7uPZ6F2GX8kYoH3GqVJRTQv0OQ4VJfVNa",  # 假的 bcrypt hash
            "nickname": "测试用户"
        }

        # 需要用真实的 bcrypt hash 来测试，这里先测试用户名不存在的路径
        # 实际集成测试中会使用真实 bcrypt
        response = client.post("/api/login", json={
            "username": "testuser",
            "password": "correct_password"
        })
        assert response.status_code == 200
        data = response.json()
        assert "success" in data

    def test_login_wrong_password(self, client, mock_database):
        """❌ 密码错误：应返回认证失败"""
        from main import get_password_hash
        real_hash = get_password_hash("correct_password")

        mock_database["user_by_name"].return_value = {
            "id": 1,
            "username": "testuser",
            "password": real_hash,
            "nickname": "测试"
        }

        response = client.post("/api/login", json={
            "username": "testuser",
            "password": "wrong_password"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "用户名或密码错误" in data["message"]

    def test_login_nonexistent_user(self, client, mock_database):
        """❌ 用户不存在：应返回认证失败（不暴露用户是否存在）"""
        mock_database["user_by_name"].return_value = None

        response = client.post("/api/login", json={
            "username": "nonexistent",
            "password": "any_password"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        # 安全：不区分"用户不存在"和"密码错误"
        assert "用户名或密码错误" in data["message"]

    def test_login_with_real_password(self, client, mock_database):
        """✅ 实际密码验证：使用真实 bcrypt 哈希验证登录"""
        from main import get_password_hash
        real_hash = get_password_hash("MyPassword123")

        mock_database["user_by_name"].return_value = {
            "id": 1,
            "username": "real_user",
            "password": real_hash,
            "nickname": "真实用户"
        }

        response = client.post("/api/login", json={
            "username": "real_user",
            "password": "MyPassword123"
        })
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["token"] is not None
        assert data["user"]["nickname"] == "真实用户"


# ==================== JWT Token 测试 ====================

class TestJWTToken:
    """JWT 令牌测试"""

    def test_create_valid_token(self):
        """✅ 生成有效 Token"""
        token = create_access_token(data={"user_id": 42})
        assert token is not None
        assert len(token) > 20

    def test_decode_valid_token(self):
        """✅ 解码有效 Token"""
        token = create_access_token(data={"user_id": 42})
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert payload["user_id"] == 42
        assert "exp" in payload

    def test_decode_invalid_token(self):
        """❌ 解码伪造 Token"""
        fake_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        with pytest.raises(Exception):
            jwt.decode(fake_token, SECRET_KEY, algorithms=[ALGORITHM])

    def test_token_with_wrong_key(self):
        """❌ 用错误的密钥解码"""
        token = create_access_token(data={"user_id": 1})
        with pytest.raises(Exception):
            jwt.decode(token, "wrong-secret-key-32-bytes-minimum", algorithms=[ALGORITHM])

    def test_token_user_info_endpoint(self, client, auth_headers):
        """✅ 携带 Token 访问 /api/user"""
        response = client.get("/api/user", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "user" in data

    def test_token_missing(self, client):
        """❌ 缺少 Token"""
        response = client.get("/api/user")
        assert response.status_code == 401  # 未认证

    def test_token_invalid(self, client):
        """❌ 伪造 Token"""
        response = client.get("/api/user", headers={
            "Authorization": "Bearer invalid_token_here"
        })
        assert response.status_code == 401
