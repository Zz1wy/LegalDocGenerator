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


class _FakeRequest:
    """只实现 get_client_ip 用到的两个属性，避免构造完整的 ASGI scope"""

    def __init__(self, peer: str, forwarded_for: str | None = None):
        self.client = type("Client", (), {"host": peer})()
        self.headers = {}
        if forwarded_for is not None:
            self.headers["x-forwarded-for"] = forwarded_for


class TestClientIpResolution:
    """
    反向代理下的真实 IP 解析。

    Docker 部署时所有请求都来自 Nginx 容器，若不解析 X-Forwarded-For，
    全站用户会共享一个限流桶；但无条件信任该头部又会让客户端可以伪造 IP。
    """

    def test_xff_ignored_when_proxy_trust_disabled(self, monkeypatch):
        """未开启代理信任时，X-Forwarded-For 一律忽略"""
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", False)
        request = _FakeRequest("203.0.113.7", "1.2.3.4")
        assert main.get_client_ip(request) == "203.0.113.7"

    def test_xff_used_from_trusted_proxy(self, monkeypatch):
        """对端是可信代理时，取 X-Forwarded-For 中的真实客户端 IP"""
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(
            main, "TRUSTED_PROXY_NETWORKS",
            main._parse_trusted_networks("172.16.0.0/12"),
        )
        request = _FakeRequest("172.18.0.2", "198.51.100.23")
        assert main.get_client_ip(request) == "198.51.100.23"

    def test_xff_ignored_from_untrusted_peer(self, monkeypatch):
        """对端不可信：即使带了 X-Forwarded-For 也不能采信（防伪造）"""
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(
            main, "TRUSTED_PROXY_NETWORKS",
            main._parse_trusted_networks("172.16.0.0/12"),
        )
        request = _FakeRequest("203.0.113.7", "1.2.3.4")
        assert main.get_client_ip(request) == "203.0.113.7"

    def test_spoofed_leftmost_entry_is_ignored(self, monkeypatch):
        """
        客户端可以自己塞一个 X-Forwarded-For，Nginx 会把真实地址追加在右边：
            6.6.6.6, 198.51.100.23
        从右往左找第一个不可信地址，得到真实 IP，而不是伪造的 6.6.6.6。
        """
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(
            main, "TRUSTED_PROXY_NETWORKS",
            main._parse_trusted_networks("172.16.0.0/12"),
        )
        request = _FakeRequest("172.18.0.2", "6.6.6.6, 198.51.100.23")
        assert main.get_client_ip(request) == "198.51.100.23"

    def test_private_client_ip_is_not_mistaken_for_proxy(self, monkeypatch):
        """
        真实客户端是私网地址（企业内网常见）时，不能因为它在可信网段里就被
        跳过 —— 否则伪造的左侧条目会生效。只取 Nginx 追加的最右一项。
        """
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(
            main, "TRUSTED_PROXY_NETWORKS",
            main._parse_trusted_networks("172.16.0.0/12,192.168.0.0/16"),
        )
        request = _FakeRequest("172.18.0.2", "6.6.6.6, 192.168.1.50")
        assert main.get_client_ip(request) == "192.168.1.50"

    def test_rightmost_entry_is_used_for_multi_hop(self, monkeypatch):
        """多级代理时也只取最右一项（由直连代理追加）"""
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(
            main, "TRUSTED_PROXY_NETWORKS",
            main._parse_trusted_networks("172.16.0.0/12"),
        )
        request = _FakeRequest("172.18.0.2", "172.18.0.3, 172.18.0.4")
        assert main.get_client_ip(request) == "172.18.0.4"

    def test_garbage_xff_is_skipped(self, monkeypatch):
        """XFF 里混入非法地址时，只取最右的合法值"""
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(
            main, "TRUSTED_PROXY_NETWORKS",
            main._parse_trusted_networks("172.16.0.0/12"),
        )
        request = _FakeRequest("172.18.0.2", "not-an-ip, 198.51.100.23")
        assert main.get_client_ip(request) == "198.51.100.23"

    def test_invalid_rightmost_falls_back_to_peer(self, monkeypatch):
        """最右一项非法时退回对端地址，不能拿垃圾字符串当 key"""
        monkeypatch.setattr(main, "TRUST_PROXY_HEADERS", True)
        monkeypatch.setattr(
            main, "TRUSTED_PROXY_NETWORKS",
            main._parse_trusted_networks("172.16.0.0/12"),
        )
        request = _FakeRequest("172.18.0.2", "198.51.100.23, not-an-ip")
        assert main.get_client_ip(request) == "172.18.0.2"


class TestBucketPruning:
    """限流计数器不能无限增长（否则是内存泄漏）"""

    def test_prune_removes_expired_buckets(self):
        max_window = max(window for _, window in RATE_LIMIT_RULES.values())
        main._rate_buckets[("1.1.1.1", "/api/login")] = [0.0, 1]
        main._rate_buckets[("2.2.2.2", "/api/login")] = [max_window + 10.0, 1]

        removed = main.prune_rate_buckets(max_window + 20.0)

        assert removed == 1
        assert ("1.1.1.1", "/api/login") not in main._rate_buckets
        assert ("2.2.2.2", "/api/login") in main._rate_buckets


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
