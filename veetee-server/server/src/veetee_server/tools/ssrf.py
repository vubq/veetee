"""SSRF guard for outbound tool integrations.

Decision (locked, M6.6): external MCP endpoints are HTTPS-only and restricted
to hosts configured in the environment allowlist. Every request re-resolves
DNS and rejects loopback/private/link-local/reserved targets to prevent SSRF.
Redirects are never followed automatically; each hop is re-validated through
the same policy. The connected peer address must match the validated DNS set,
which closes the DNS-rebinding window between validation and connection.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

#: Resolves a hostname to a list of IP address strings. Injectable so tests
#: never touch real DNS.
Resolver = Callable[[str], list[str]]


class ExternalUrlPolicyError(Exception):
    """Raised when an outbound URL or resolved target violates the policy."""


def _default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise ExternalUrlPolicyError(f"DNS resolution failed for host: {host}") from exc
    return [str(info[4][0]) for info in infos]


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_unspecified or ip.is_loopback or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_reserved or ip.is_private:
        return True
    # 0.0.0.0/8 style networks and IPv4-mapped IPv6 must not smuggle private
    # ranges past the check.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_private or not ip.ipv4_mapped.is_global
    return not ip.is_global


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    """An outbound target that passed the static URL policy."""

    url: str
    scheme: str
    host: str
    port: int
    path_with_query: str


def _parse_port(url_parts: SplitResult) -> int:
    if url_parts.port is None:
        return 443
    if not 1 <= url_parts.port <= 65535:
        raise ExternalUrlPolicyError("URL port must be between 1 and 65535")
    return int(url_parts.port)


class ExternalURLPolicy:
    """Static + runtime URL policy shared by every outbound integration."""

    def __init__(
        self,
        allowed_hosts: list[str],
        resolver: Resolver | None = None,
    ) -> None:
        normalized: list[str] = []
        for raw in allowed_hosts:
            host = raw.strip().casefold()
            if host:
                normalized.append(host)
        self.allowed_hosts = frozenset(normalized)
        self._resolver: Resolver = resolver or _default_resolver

    def validate_url(self, url: str) -> ValidatedTarget:
        """Validates scheme, credentials and allowlisted host without DNS."""
        if not url or not url.strip():
            raise ExternalUrlPolicyError("URL must be a non-empty string")
        try:
            parts = urlsplit(url.strip())
            port = _parse_port(parts)
        except ValueError as exc:
            raise ExternalUrlPolicyError(f"Invalid URL: {exc}") from exc

        if parts.scheme.casefold() != "https":
            raise ExternalUrlPolicyError("External integrations require HTTPS")
        if parts.username or parts.password or "@" in parts.netloc:
            raise ExternalUrlPolicyError("URL must not contain userinfo or credentials")
        if parts.fragment:
            raise ExternalUrlPolicyError("URL must not contain a fragment")

        hostname = (parts.hostname or "").casefold()
        if not hostname:
            raise ExternalUrlPolicyError("URL must contain a valid host")
        if hostname not in self.allowed_hosts:
            raise ExternalUrlPolicyError(
                f"Host '{hostname}' is not in the external integration allowlist"
            )

        path_with_query = parts.path or "/"
        if parts.query:
            path_with_query = f"{path_with_query}?{parts.query}"
        return ValidatedTarget(
            url=url.strip(),
            scheme="https",
            host=hostname,
            port=port,
            path_with_query=path_with_query,
        )

    def resolve_validated_ips(
        self, target_host: str
    ) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...]:
        """Resolves DNS and rejects any non-global address (SSRF defense)."""
        addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
        seen: set[str] = set()
        for raw_ip in self._resolver(target_host):
            if raw_ip in seen:
                continue
            seen.add(raw_ip)
            try:
                ip = ipaddress.ip_address(raw_ip)
            except ValueError as exc:
                raise ExternalUrlPolicyError(
                    f"Resolver returned an invalid address for host {target_host}"
                ) from exc
            if _is_blocked_address(ip):
                raise ExternalUrlPolicyError(
                    f"Resolved address for host {target_host} points into a "
                    "blocked network range"
                )
            addresses.append(ip)
        if not addresses:
            raise ExternalUrlPolicyError(f"Host {target_host} resolved to no addresses")
        return tuple(addresses)

    def validate_target_for_request(
        self, url: str
    ) -> tuple[
        ValidatedTarget,
        tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
    ]:
        """Full pre-request validation: static policy plus fresh DNS checks."""
        target = self.validate_url(url)
        return target, self.resolve_validated_ips(target.host)

    @staticmethod
    def assert_peer_allowed(
        peer_ip: str | None,
        allowed_addresses: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
    ) -> None:
        """Fails closed unless the connected peer matches validated DNS."""
        if not peer_ip:
            raise ExternalUrlPolicyError(
                "Connection peer address unavailable; refusing response"
            )
        try:
            peer = ipaddress.ip_address(peer_ip)
        except ValueError as exc:
            raise ExternalUrlPolicyError("Connection peer address is invalid") from exc
        if isinstance(peer, ipaddress.IPv6Address) and peer.ipv4_mapped is not None:
            peer = peer.ipv4_mapped
        if peer not in allowed_addresses:
            raise ExternalUrlPolicyError(
                "Connected peer does not match validated DNS records "
                "(possible DNS rebinding)"
            )
