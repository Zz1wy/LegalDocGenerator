"""
限流逻辑测试

分两层测：
1. check_rate_limit —— 纯函数，直接构造时间序列验证「固定窗口计数器」的边界行为；
2. 中间件 —— 真发 HTTP 请求，验证超限确实返回 429 + Retry-After。

为什么要把限流做成「纯函数 + 薄中间件」两层：
    check_rate_limit 的时钟是从参数传进来的，不是内部读 time.monotonic()，
    所以测试可以用 0.0 / window+1 这样的假时间精确地验证「窗口过期后计数归零」，
    不用真的 sleep。
"""

import pytest

import main
from main import check_rate_limit, RATE_LIMIT_RULES


@pytest.fixture(autouse=True)
def _clean_buckets():
    """每个用例前后都清空计数器，避免用例之间互相污染"""
    main._rate_buckets.clear()
    yield
    main._rate_buckets.clear()


class TestCheckRateLimit:
    """固定窗口计数器本身的边界行为"""

    def test_path_without_rule_is_unlimited(self):
        """没有配置规则的路径不受限流影响"""
        for _ in range(1000):
            assert check_rate_limit("1.2.3.4", "/api/doc-types", 0.0) is None

    def test_allows_up_to_limit_then_rejects(self):
        """/api/login 的规则是 10 次/分钟：前 10 次放行，第 11 次拒绝"""
        limit, _ = RATE_LIMIT_RULES["/api/login"]
        for i in range(limit):
            assert check_rate_limit("1.2.3.4", "/api/login", 0.0) is None, f"第 {i + 1} 次应放行"
        assert check_rate_limit("1.2.3.4", "/api/login", 0.0) is not None

    def test_window_resets_after_expiry(self):
        """窗口过期后计数归零，重新放行"""
        limit, window = RATE_LIMIT_RULES["/api/login"]
        for _ in range(limit):
            check_rate_limit("1.2.3.4", "/api/login", 0.0)
        assert check_rate_limit("1.2.3.4", "/api/login", 0.0) is not None

        # 时间推进到窗口之外（这里用假时间，不需要真的 sleep）
        assert check_rate_limit("1.2.3.4", "/api/login", window + 1) is None

    def test_limits_are_per_ip(self):
        """限流按 IP 隔离：一个 IP 打满不影响另一个 IP"""
        limit, _ = RATE_LIMIT_RULES["/api/login"]
        for _ in range(limit + 1):
            check_rate_limit("10.0.0.1", "/api/login", 0.0)
        assert check_rate_limit("10.0.0.2", "/api/login", 0.0) is None

    def test_limits_are_per_path(self):
        """同一个 IP 打满 /api/login 不应牵连 /api/generate"""
        limit, _ = RATE_LIMIT_RULES["/api/login"]
        for _ in range(limit + 1):
            check_rate_limit("10.0.0.1", "/api/login", 0.0)
        assert check_rate_limit("10.0.0.1", "/api/generate", 0.0) is None

    def test_retry_after_is_positive(self):
        """Retry-After 必须是正整数（HTTP 头不允许 0 或负数）"""
        limit, _ = RATE_LIMIT_RULES["/api/login"]
        retry_after = None
        for _ in range(limit + 1):
            retry_after = check_rate_limit("1.2.3.4", "/api/login", 0.0)
        assert retry_after is not None
        assert retry_after >= 1


class TestRateLimitMiddleware:
    """端到端：真的发请求，看是否返回 429"""

    def test_returns_429_when_exceeded(self, client, monkeypatch):
        """超过限额的请求应被拒绝"""
        monkeypatch.setattr(main, "RATE_LIMIT_ENABLED", True)
        limit, _ = RATE_LIMIT_RULES["/api/login"]

        statuses = [
            client.post("/api/login", json={"username": "u", "password": "p"}).status_code
            for _ in range(limit + 1)
        ]
        assert 429 in statuses, f"应当出现 429，实际状态码序列: {statuses}"

    def test_429_response_shape(self, client, monkeypatch):
        """429 响应应带 success=false 和 Retry-After 头"""
        monkeypatch.setattr(main, "RATE_LIMIT_ENABLED", True)
        limit, _ = RATE_LIMIT_RULES["/api/login"]

        response = None
        for _ in range(limit + 1):
            response = client.post("/api/login", json={"username": "u", "password": "p"})

        assert response.status_code == 429
        assert response.json()["success"] is False
        assert "Retry-After" in response.headers

    def test_unlimited_path_unaffected(self, client, monkeypatch):
        """没配规则的接口不会被误伤"""
        monkeypatch.setattr(main, "RATE_LIMIT_ENABLED", True)
        for _ in range(50):
            assert client.get("/api/doc-types").status_code == 200
