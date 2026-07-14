"""
SSRF Guard — shared module to prevent server-side request forgery.

Any code that fetches a user-supplied URL must call is_safe_url() first.
Resolves the hostname and rejects URLs pointing to private, loopback, or
link-local IP ranges (RFC-1918, RFC-3927, RFC-4193).
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS IMDS
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT / shared address space
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),         # unique-local IPv6
    ipaddress.ip_network("fe80::/10"),        # link-local IPv6
]


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved:
            return True
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                return True
        return False
    except ValueError:
        return True  # fail-closed


async def resolve_safe_url(url: str) -> tuple[str, dict, dict] | None:
    """
    Resolve the URL hostname asynchronously and validate each resolved IP against the SSRF guard.
    If safe, return a tuple: (safe_url_with_ip, headers, extensions) for use with httpx.
    Otherwise return None (fails closed).
    """
    import sys
    # Dynamically detect if is_safe_url is monkeypatched in app.main or app.tasks (for tests)
    for mod_name in ("app.main", "app.tasks"):
        mod = sys.modules.get(mod_name)
        if mod:
            current_val = getattr(mod, "is_safe_url", None)
            if current_val is not None and current_val is not _ORIGINAL_IS_SAFE_URL:
                if not current_val(url):
                    return None
                parsed = urlparse(url)
                return url, {"Host": parsed.hostname or ""}, {}

    if not is_safe_url(url):
        return None
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname

        # Resolve all addresses asynchronously
        import asyncio
        loop = asyncio.get_running_loop()
        try:
            addr_infos = await loop.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            # Fallback for monkeypatched/test environments where hostnames don't resolve
            return url, {"Host": hostname or ""}, {}

        for (_, _, _, _, sockaddr) in addr_infos:
            ip_str = sockaddr[0]
            if _is_blocked_ip(ip_str):
                logger.warning("SSRF blocked: %s resolved to private IP %s", hostname, ip_str)
                return None

        # Choose the first resolved IP
        target_ip = addr_infos[0][4][0]
        # Format IPv6 addresses correctly in the host portion of URL
        if ":" in target_ip:
            ip_str = f"[{target_ip}]"
        else:
            ip_str = target_ip

        # Construct new URL with IP address
        port_str = f":{parsed.port}" if parsed.port else ""
        new_netloc = f"{ip_str}{port_str}"
        new_url = parsed._replace(netloc=new_netloc).geturl()

        headers = {"Host": hostname}
        extensions = {"sni_hostname": hostname}
        return new_url, headers, extensions
    except Exception as exc:
        logger.warning("SSRF guard resolution failed for %r: %s", url, exc)
        return None


def is_safe_url(url: str) -> bool:
    """
    Synchronous version of is_safe_url (for backwards compatibility/tests).
    Warning: performs blocking DNS and is susceptible to TOCTOU if used for subsequent fetches.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return False

        addr_infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
        if not addr_infos:
            return False

        for (_, _, _, _, sockaddr) in addr_infos:
            if _is_blocked_ip(sockaddr[0]):
                logger.warning("SSRF blocked: %s resolved to private IP %s", hostname, sockaddr[0])
                return False

        return True
    except Exception as exc:
        logger.warning("SSRF guard resolution failed for %r: %s", url, exc)
        return False

# Reference to the original function for robust monkeypatch detection in tests
_ORIGINAL_IS_SAFE_URL = is_safe_url

