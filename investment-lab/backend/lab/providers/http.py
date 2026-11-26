"""Outbound HTTP with SSRF protection, retries (exponential backoff + jitter) and quota tracking."""
from __future__ import annotations

import ipaddress
import random
import socket
import time
from urllib.parse import urlparse

import httpx

from ..config import get_settings

ALLOWED_HOSTS = {
    "api.openalex.org", "api.crossref.org", "efts.sec.gov", "data.sec.gov", "www.sec.gov",
    "api.tiingo.com", "www.alphavantage.co", "stooq.com", "oauth.reddit.com", "www.reddit.com",
    "api.x.com", "api.twitter.com", "doi.org", "api.fda.gov",
}


class UnsafeURLError(ValueError):
    pass


class ProviderError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None, retryable: bool = True):
        super().__init__(msg)
        self.status = status
        self.retryable = retryable


def assert_safe_url(url: str, allowed_hosts: set[str] | None = None, resolve: bool = True) -> str:
    """Reject non-https, credentials in URL, non-allowlisted hosts and private/metadata addresses."""
    p = urlparse(url)
    if p.scheme != "https":
        raise UnsafeURLError(f"only https URLs are allowed: {url!r}")
    if p.username or p.password:
        raise UnsafeURLError("credentials in URL are not allowed")
    host = (p.hostname or "").lower()
    if not host:
        raise UnsafeURLError("missing host")
    try:
        ip = ipaddress.ip_address(host)
        _check_ip(ip)
    except ValueError as e:
        if isinstance(e, UnsafeURLError):
            raise
    hosts = allowed_hosts if allowed_hosts is not None else ALLOWED_HOSTS
    if hosts and host not in hosts:
        raise UnsafeURLError(f"host not allowlisted: {host}")
    if resolve:
        try:
            for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP):
                _check_ip(ipaddress.ip_address(info[4][0]))
        except socket.gaierror:
            pass  # resolution may happen in an egress proxy; host allowlist still applies
    return url


def _check_ip(ip):
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
            or ip.is_unspecified or str(ip).startswith("169.254.") or str(ip) == "100.100.100.200"):
        raise UnsafeURLError(f"address not allowed: {ip}")


def is_safe_display_url(url: str) -> bool:
    """For rendering stored citation links: https only, no private hosts. (No allowlist.)"""
    try:
        assert_safe_url(url, allowed_hosts=set(), resolve=False)
        return True
    except UnsafeURLError:
        return False


def get(url: str, params: dict | None = None, headers: dict | None = None, retries: int = 3,
        timeout: float | None = None, on_call=None) -> httpx.Response:
    assert_safe_url(url)
    timeout = timeout or get_settings().http_timeout_seconds
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if on_call:
                on_call()
            with httpx.Client(timeout=timeout, follow_redirects=False) as c:
                r = c.get(url, params=params, headers=headers)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location", "")
                assert_safe_url(loc)
                url = loc
                continue
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("retry-after")
                delay = float(ra) if ra and ra.isdigit() else (2 ** attempt) + random.uniform(0, 0.5)
                last = ProviderError(f"HTTP {r.status_code} from {urlparse(url).hostname}", r.status_code)
                if attempt < retries:
                    time.sleep(min(delay, 30))
                    continue
                raise last
            if r.status_code >= 400:
                raise ProviderError(f"HTTP {r.status_code} from {urlparse(url).hostname}", r.status_code, retryable=False)
            return r
        except httpx.HTTPError as e:
            last = ProviderError(f"network error contacting {urlparse(url).hostname}: {type(e).__name__}")
            if attempt < retries:
                time.sleep((2 ** attempt) + random.uniform(0, 0.5))
                continue
    raise last or ProviderError("request failed")
