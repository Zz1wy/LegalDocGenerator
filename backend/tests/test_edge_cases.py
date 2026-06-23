"""
边界值与安全性测试

测试覆盖:
- 超长输入
- 空值 / Null / 缺失字段
- 特殊字符 / Unicode / Emoji
- SQL 注入 / XSS 攻击
- 并发请求
- HTTP 方法错误
- Content-Type 错误
"""

import pytest
import json
from concurrent.futures import ThreadPoolExecutor
import time


# ==================== 边界值 ====================

class TestBoundaryValues:
    """边界值测试"""

    # ── 字符串边界 ──

    @pytest.mark.parametrize("field_value", [
        "",                          # 空字符串
        "a",                         # 单字符
        "a" * 100,                   # 中等长度
        "a" * 1000,                  # 长文本
        "a" * 10000,                 # 超长文本
        "中文测试" * 500,            # 中文长文本
    ])
    def test_various_input_lengths(self, client, auth_headers, field_value):
        """🔲 各种输入长度"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {"party_a": field_value}
        }, headers=auth_headers)
        assert response.status_code == 200, f"长度 {len(field_value)} 导致崩溃"

    # ── JSON 边界 ──

    def test_empty_json_body(self, client):
        """🔲 空请求体"""
        response = client.post("/api/generate", content=b"")
        assert response.status_code in [200, 400, 422]  # 不应 500

    def test_malformed_json(self, client):
        """🔲 畸形的 JSON"""
        response = client.post("/api/generate", content=b"{broken json")
        assert response.status_code in [400, 422]  # 应返回错误

    def test_extra_fields_in_request(self, client, auth_headers):
        """🔲 多余字段：应正常忽略"""
        response = client.post("/api/generate", json={
            "doc_type": "借条",
            "fields": {"lender": "张三"},
            "unexpected_field": "should_be_ignored",
            "another_extra": 12345
        }, headers=auth_headers)
        assert response.status_code == 200

    def test_missing_required_fields(self, client):
        """❌ 缺少必填字段"""
        response = client.post("/api/generate", json={})
        assert response.status_code == 422  # pydantic 验证失败

    def test_wrong_field_types(self, client):
        """❌ 字段类型错误"""
        response = client.post("/api/generate", json={
            "doc_type": 12345,           # 应为字符串
            "fields": "not_a_dict"       # 应为字典
        })
        assert response.status_code == 422

    def test_null_values(self, client, auth_headers):
        """🔲 Null 值字段"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {"party_a": None, "party_b": None}
        }, headers=auth_headers)
        assert response.status_code in [200, 422]

    # ── 数字边界 ──

    def test_history_limit_boundaries(self, client, auth_headers):
        """🔲 各种 limit 值"""
        for limit in [0, 1, 50, 100, 1000]:
            response = client.get(f"/api/history?limit={limit}", headers=auth_headers)
            assert response.status_code == 200, f"limit={limit} 失败"

    def test_history_limit_negative(self, client, auth_headers):
        """🔲 负数 limit"""
        response = client.get("/api/history?limit=-1", headers=auth_headers)
        assert response.status_code == 200  # 不应崩溃

    def test_history_limit_string(self, client, auth_headers):
        """🔲 非数字 limit"""
        response = client.get("/api/history?limit=abc", headers=auth_headers)
        assert response.status_code == 422  # 验证失败

    def test_delete_record_id_string(self, client, auth_headers):
        """🔲 非数字 record_id"""
        response = client.delete("/api/history/abc", headers=auth_headers)
        assert response.status_code == 422  # 验证失败


# ==================== 安全性 ====================

class TestSecurity:
    """安全性测试"""

    SQL_INJECTION_PAYLOADS = [
        "'; DROP TABLE users; --",
        "' OR '1'='1",
        "1; DELETE FROM generated_records WHERE 1=1; --",
        "'; SELECT * FROM users; --",
        "admin'--",
        "1' UNION SELECT * FROM users--",
    ]

    XSS_PAYLOADS = [
        "<script>alert('xss')</script>",
        "<img src=x onerror=alert(1)>",
        "javascript:alert(1)",
        "<iframe src='evil.com'>",
        "{{constructor.constructor('alert(1)')()}}",
    ]

    @pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_fields(self, client, auth_headers, payload):
        """🔒 SQL 注入：字段值中的 SQL 不应被执行"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {"party_a": payload}
        }, headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_doc_type(self, client, auth_headers, payload):
        """🔒 SQL 注入：文书类型中的 SQL"""
        response = client.post("/api/generate", json={
            "doc_type": payload,
            "fields": {"test": "value"}
        }, headers=auth_headers)
        assert response.status_code == 200

    @pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
    def test_sql_injection_in_login(self, client, payload):
        """🔒 SQL 注入：登录字段"""
        response = client.post("/api/login", json={
            "username": payload,
            "password": payload
        })
        assert response.status_code == 200

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_xss_in_fields(self, client, auth_headers, payload):
        """🔒 XSS：字段值中的脚本"""
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {"party_a": payload}
        }, headers=auth_headers)
        assert response.status_code == 200

    def test_no_sensitive_data_leak(self, client):
        """🔒 错误响应不应泄露敏感信息"""
        response = client.post("/api/login", json={
            "username": "test",
            "password": "wrong"
        })
        data = response.json()
        # 不应包含堆栈跟踪或数据库信息
        assert "password" not in str(data.get("message", "")).lower()
        assert "stack" not in str(data).lower()
        assert "traceback" not in str(data).lower()

    def test_auth_token_not_leaked(self, client, auth_headers):
        """🔒 Token 不应出现在响应体的非必要位置"""
        response = client.get("/api/history", headers=auth_headers)
        data = response.json()
        # 响应中不应出现原始 token
        response_str = json.dumps(data)
        token = auth_headers["Authorization"].replace("Bearer ", "")
        assert token not in response_str


# ==================== HTTP 协议级 ====================

class TestHTTPEdgeCases:
    """HTTP 协议边界"""

    def test_wrong_method_post_to_get(self, client):
        """❌ 错误 HTTP 方法"""
        response = client.post("/api/doc-types")  # 应为 GET
        assert response.status_code in [200, 405]  # 不应 500

    def test_wrong_method_get_to_post(self, client):
        """❌ 错误 HTTP 方法"""
        response = client.get("/api/generate")  # 应为 POST
        assert response.status_code in [405, 422]  # 不应 500

    def test_incorrect_content_type(self, client):
        """🔲 错误的 Content-Type"""
        response = client.post("/api/generate",
            data="doc_type=test&fields={}",
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        assert response.status_code in [200, 400, 415, 422]

    def test_large_request_body(self, client, auth_headers):
        """🔲 超大请求体（1MB 字符串）"""
        response = client.post("/api/generate", json={
            "doc_type": "借条",
            "fields": {"lender": "A" * 1024 * 1024}
        }, headers=auth_headers)
        assert response.status_code in [200, 413, 422]

    def test_unicode_normalization(self, client, auth_headers):
        """🔲 Unicode 规范化"""
        # 全角和半角字符
        response = client.post("/api/generate", json={
            "doc_type": "劳动合同",
            "fields": {
                "party_a": "ＡＢＣ株式会社",  # 全角
                "salary": "３０００元/月"      # 全角数字
            }
        }, headers=auth_headers)
        assert response.status_code == 200

    def test_health_check(self, client):
        """✅ 健康检查"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "rag" in data  # 应包含 RAG 状态


# ==================== 并发 ====================

class TestConcurrency:
    """并发请求测试"""

    def test_concurrent_generate_requests(self, client, auth_headers):
        """🔲 10 个并发生成请求"""
        def make_request(i):
            return client.post("/api/generate", json={
                "doc_type": "劳动合同",
                "fields": {"party_a": f"公司{i}"}
            }, headers=auth_headers)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(make_request, i) for i in range(10)]
            results = [f.result() for f in futures]

        # 所有请求都应返回 200
        for i, r in enumerate(results):
            assert r.status_code == 200, f"并发请求 {i} 失败: {r.status_code}"
            data = r.json()
            assert data["success"] is True, f"并发请求 {i}: {data.get('message')}"

    def test_concurrent_login_requests(self, client):
        """🔲 5 个并发登录请求"""
        def make_request(i):
            return client.post("/api/login", json={
                "username": f"user{i}",
                "password": "password"
            })

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(make_request, i) for i in range(5)]
            results = [f.result() for f in futures]

        for r in results:
            assert r.status_code == 200
