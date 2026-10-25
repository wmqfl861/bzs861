"""SSRF protection enforced where connections are made.

Rules (ADR-0002): only http/https; no credentials in URLs; IP-literal hosts must be
public; hostnames are resolved by ``GuardedResolver`` and only public addresses are
handed to the connector, so the address that is actually connected is the one that
was checked (DNS rebinding safe). Redirects are re-validated hop by hop by the fetcher.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Iterable
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver
from yarl import URL

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_LOCAL_NAT64 = ipaddress.ip_network("64:ff9b:1::/48")
_NUMERIC_HOST = re.compile(r"^(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+))*$", re.I)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")
_TEST_LOOPBACK = ipaddress.ip_network("127.0.0.1/32")


class PolicyError(Exception):
    """The request target is not allowed by the network policy."""


class NetPolicy:
    def __init__(self, allow_cidrs: Iterable[str] = (), *, test_mode: bool = False):
        self.test_mode = test_mode
        self.allow = [ipaddress.ip_network(c, strict=False) for c in allow_cidrs]
        if self.allow and (not test_mode or any(net != _TEST_LOOPBACK for net in self.allow)):
            raise ValueError("allow_cidrs is disabled in production; test mode permits only 127.0.0.1/32")

    def ip_allowed(self, value: str | ipaddress._BaseAddress) -> bool:
        ip = ipaddress.ip_address(value) if not isinstance(value, ipaddress._BaseAddress) else value
        if any(ip in net for net in self.allow):
            return True
        if isinstance(ip, ipaddress.IPv6Address):
            # The local-use /48 has multiple embedded-IPv4 layouts. It is never a public target.
            if ip in _LOCAL_NAT64:
                return False
            embedded = [ip.ipv4_mapped, ip.sixtofour]
            if ip.teredo:
                embedded.extend(ip.teredo)
            if ip in _NAT64:
                embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
            for inner in embedded:
                if inner is not None and not self.ip_allowed(inner):
                    return False
        return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback
                                    or ip.is_link_local or ip.is_unspecified)

    def prepare_url(self, url: str) -> URL:
        """Validate the same canonical URL that aiohttp will connect to, including IDNA mapping."""
        if not isinstance(url, str):
            raise PolicyError("URL must be a string")
        if len(url) > 4096:
            raise PolicyError("URL too long")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
            raise PolicyError("control characters in URL are not allowed")
        try:
            # yarl may normalize empty userinfo away. Reject the original
            # authority syntax before that irreversible normalization.
            if "@" in urlsplit(url).netloc:
                raise PolicyError("credentials in URL are not allowed")
            parts = URL(url)
            port = parts.port
        except (ValueError, UnicodeError) as exc:
            raise PolicyError(f"invalid URL: {exc}") from exc
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise PolicyError(f"scheme not allowed: {scheme or 'none'}")
        if parts.user is not None or parts.password is not None:
            raise PolicyError("credentials in URL are not allowed")
        # raw_host is the ASCII/IDNA host used by the connector. Checking the original Unicode
        # spelling would allow e.g. 127\u30020\u30020\u30021 to become a private IP after this check.
        host = (parts.raw_host or "").rstrip(".").lower()
        if not host:
            raise PolicyError("missing host")
        if "%" in host:
            raise PolicyError("scoped or percent-encoded hosts are not allowed")
        if port is None or not 1 <= port <= 65535:
            raise PolicyError("invalid port")
        if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
            if not self._host_allowed_for_tests(host):
                raise PolicyError("local hostnames are not fetched")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            if not self.ip_allowed(ip):
                raise PolicyError(f"address not public: {host}")
        elif _NUMERIC_HOST.match(host):
            raise PolicyError("numeric host forms are not fetched")
        try:
            return parts.with_host(host)
        except ValueError as exc:
            raise PolicyError(f"invalid hostname: {exc}") from exc

    def check_url(self, url: str) -> tuple[str, str, int]:
        """Validate scheme/host/port; returns (scheme, canonical host, port)."""
        parts = self.prepare_url(url)
        assert parts.raw_host is not None and parts.port is not None
        return parts.scheme, parts.raw_host, parts.port

    def _host_allowed_for_tests(self, host: str) -> bool:
        return host == "localhost" and any(ipaddress.ip_address("127.0.0.1") in n for n in self.allow)


class GuardedResolver(AbstractResolver):
    """Resolves names and drops every non-public address before aiohttp connects.

    Fixed loopback DNS answers are accepted only by an explicitly constructed test policy.
    """

    def __init__(self, policy: NetPolicy, test_hosts: dict[str, str] | None = None):
        self.policy = policy
        self.test_hosts = dict(test_hosts or {})
        if self.test_hosts and (not policy.test_mode or any(
                not host.endswith(".test") or ip != "127.0.0.1" for host, ip in self.test_hosts.items())):
            raise ValueError("fixed DNS answers require test mode and a .test name mapped to 127.0.0.1")
        self._inner = aiohttp.ThreadedResolver()

    async def _lookup(self, host: str, port: int, family: socket.AddressFamily) -> list[dict]:
        if host in self.test_hosts:
            return [{"hostname": host, "host": self.test_hosts[host], "port": port, "family": socket.AF_INET,
                     "proto": 0, "flags": socket.AI_NUMERICHOST}]
        return await self._inner.resolve(host, port, family)  # OSError -> reported as network_error

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET):
        infos = await self._lookup(host, port, family)
        allowed = [info for info in infos if self.policy.ip_allowed(info["host"])]
        if not allowed:
            raise PolicyError(f"{host} resolves only to non-public addresses")
        return allowed

    async def close(self) -> None:
        await self._inner.close()
