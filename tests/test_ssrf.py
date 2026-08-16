"""
Tests for app/ssrf.py — covers _is_blocked_ip and is_safe_url.
All network I/O is mocked via monkeypatch on socket.getaddrinfo.
"""
import socket
import pytest
from app.ssrf import _is_blocked_ip, is_safe_url, resolve_safe_url


# ---------------------------------------------------------------------------
# _is_blocked_ip
# ---------------------------------------------------------------------------

class TestIsBlockedIp:
    def test_loopback_ipv4(self):
        assert _is_blocked_ip("127.0.0.1") is True

    def test_private_class_a(self):
        assert _is_blocked_ip("10.0.0.1") is True

    def test_private_class_b(self):
        assert _is_blocked_ip("172.16.0.1") is True

    def test_private_class_c(self):
        assert _is_blocked_ip("192.168.1.100") is True

    def test_link_local(self):
        assert _is_blocked_ip("169.254.1.1") is True

    def test_cgnat(self):
        assert _is_blocked_ip("100.64.0.1") is True

    def test_ipv6_loopback(self):
        assert _is_blocked_ip("::1") is True

    def test_ipv6_unique_local(self):
        assert _is_blocked_ip("fc00::1") is True

    def test_ipv6_link_local(self):
        assert _is_blocked_ip("fe80::1") is True

    def test_public_ipv4(self):
        assert _is_blocked_ip("1.1.1.1") is False

    def test_public_ipv4_another(self):
        assert _is_blocked_ip("8.8.8.8") is False

    def test_invalid_string_fails_closed(self):
        # Non-IP strings should return True (fail-closed)
        assert _is_blocked_ip("not-an-ip") is True

    def test_empty_string_fails_closed(self):
        assert _is_blocked_ip("") is True


# ---------------------------------------------------------------------------
# is_safe_url
# ---------------------------------------------------------------------------

class TestIsSafeUrl:
    def _mock_getaddrinfo_public(self, host, port, **kwargs):
        """Simulates DNS resolving to a public IP."""
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 0))]

    def _mock_getaddrinfo_private(self, host, port, **kwargs):
        """Simulates DNS resolving to a private IP."""
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0))]

    def _mock_getaddrinfo_loopback(self, host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    def _mock_getaddrinfo_empty(self, host, port, **kwargs):
        return []

    def _mock_getaddrinfo_raises(self, host, port, **kwargs):
        raise socket.gaierror("Name or service not known")

    # --- Scheme checks ---
    def test_rejects_non_http_scheme(self, monkeypatch):
        assert is_safe_url("ftp://example.com/file") is False

    def test_rejects_file_scheme(self, monkeypatch):
        assert is_safe_url("file:///etc/passwd") is False

    def test_rejects_empty_string(self, monkeypatch):
        assert is_safe_url("") is False

    # --- Hostname checks ---
    def test_rejects_localhost(self, monkeypatch):
        assert is_safe_url("http://localhost/path") is False

    def test_rejects_localhost_localdomain(self, monkeypatch):
        assert is_safe_url("http://localhost.localdomain/") is False

    def test_rejects_url_with_no_hostname(self, monkeypatch):
        # urlparse("http:///path") gives empty hostname
        assert is_safe_url("http:///path") is False

    # --- DNS resolution failures ---
    def test_rejects_on_dns_failure(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", self._mock_getaddrinfo_raises)
        assert is_safe_url("http://nonexistent.invalid/path") is False

    def test_rejects_on_empty_dns_result(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", self._mock_getaddrinfo_empty)
        assert is_safe_url("http://example.com/") is False

    # --- IP blocking ---
    def test_rejects_private_ip_resolution(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", self._mock_getaddrinfo_private)
        assert is_safe_url("http://internal.corp/") is False

    def test_rejects_loopback_ip_resolution(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", self._mock_getaddrinfo_loopback)
        assert is_safe_url("http://sneaky.evil.com/") is False

    # --- Happy path ---
    def test_accepts_public_ip_resolution(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", self._mock_getaddrinfo_public)
        assert is_safe_url("http://example.com/image.jpg") is True

    def test_accepts_https(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", self._mock_getaddrinfo_public)
        assert is_safe_url("https://example.com/cam.jpg") is True

    def test_rejects_bare_private_ip_in_url(self, monkeypatch):
        """Bare IPs like 192.168.1.1 should be rejected without DNS lookup needed."""
        monkeypatch.setattr(socket, "getaddrinfo", self._mock_getaddrinfo_private)
        assert is_safe_url("http://192.168.1.1/stream") is False


class TestResolveSafeUrl:
    @pytest.mark.asyncio
    async def test_resolve_safe_url_unsafe(self):
        # is_safe_url returns False immediately for unsupported scheme
        res = await resolve_safe_url("ftp://example.com")
        assert res is None

    @pytest.mark.asyncio
    async def test_resolve_safe_url_success(self, monkeypatch):
        # Mock socket.getaddrinfo globally
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("1.1.1.1", 0))])
        
        res = await resolve_safe_url("http://example.com/image.jpg")
        assert res is not None
        safe_url, headers, extensions = res
        assert safe_url == "http://example.com/image.jpg"
        assert headers == {"Host": "example.com"}

    @pytest.mark.asyncio
    async def test_resolve_safe_url_ipv6(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2606:4700:4700::1111", 0, 0, 0))])
        
        res = await resolve_safe_url("https://example.com/cam.jpg")
        assert res is not None
        safe_url, headers, extensions = res
        assert safe_url == "https://example.com/cam.jpg"

    @pytest.mark.asyncio
    async def test_resolve_safe_url_with_port(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))])
        
        res = await resolve_safe_url("http://example.com:8080/cam.jpg")
        assert res is not None
        safe_url, headers, extensions = res
        assert safe_url == "http://example.com:8080/cam.jpg"

    @pytest.mark.asyncio
    async def test_resolve_safe_url_private_ip(self, monkeypatch):
        # Override is_safe_url to pass check, but return private IP in the actual DNS lookup
        import app.ssrf
        monkeypatch.setattr(app.ssrf, "is_safe_url", lambda url: True)
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0))])
        
        res = await resolve_safe_url("http://example.com/cam.jpg")
        assert res is None

    @pytest.mark.asyncio
    async def test_resolve_safe_url_empty_hostname(self):
        res = await resolve_safe_url("http:///path")
        assert res is None

    @pytest.mark.asyncio
    async def test_resolve_safe_url_gaierror(self, monkeypatch):
        import app.ssrf
        monkeypatch.setattr(app.ssrf, "is_safe_url", lambda url: True)
        def raise_gaierror(*a, **kw):
            raise socket.gaierror(-2, "Name or service not known")
        monkeypatch.setattr(socket, "getaddrinfo", raise_gaierror)
        
        res = await resolve_safe_url("http://example.com/cam.jpg")
        assert res is not None
        assert res[0] == "http://example.com/cam.jpg"

    @pytest.mark.asyncio
    async def test_resolve_safe_url_generic_exception(self, monkeypatch):
        import app.ssrf
        monkeypatch.setattr(app.ssrf, "is_safe_url", lambda url: True)
        def raise_runtime_error(*a, **kw):
            raise RuntimeError("Generic DNS failure")
        monkeypatch.setattr(socket, "getaddrinfo", raise_runtime_error)
        
        res = await resolve_safe_url("http://example.com/cam.jpg")
        assert res is None
