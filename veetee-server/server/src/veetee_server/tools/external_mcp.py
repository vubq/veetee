"""Outbound MCP JSON-RPC client guarded by the shared SSRF policy.

The wire format follows JSON-RPC 2.0 (``tools/list``, ``tools/call``). Every
HTTP hop — including each manual redirect — passes through the external URL
policy, re-resolves DNS and pins the connected peer address. Responses are
size-bounded, content-type checked and parsed strictly; remote error payloads
are redacted before they reach logs or exceptions.
"""

from __future__ import annotations

import asyncio
import ipaddress
import itertools
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urljoin

import httpx

from veetee_server.logging import redact
from veetee_server.tools.ssrf import ExternalURLPolicy

logger = logging.getLogger("veetee.tools.external_mcp")

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_ERROR_MESSAGE_CHARS = 200


class ExternalMCPError(Exception):
    """Base error for outbound MCP calls."""


class ExternalMCPTimeoutError(ExternalMCPError, TimeoutError):
    """Raised when the remote endpoint exceeds the request timeout."""


class ExternalMCPTransportError(ExternalMCPError):
    """Raised for connection/TLS/HTTP transport failures."""


class ExternalMCPOversizedResponseError(ExternalMCPError):
    """Raised when a response body exceeds the configured byte bound."""


class ExternalMCPBadResponseError(ExternalMCPError):
    """Raised for non-JSON content types, malformed bodies or bad statuses."""


class ExternalMCPRemoteError(ExternalMCPError):
    """Raised when the remote server returns a JSON-RPC error object."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SentResponse:
    """One HTTP hop as observed by the sender abstraction."""

    status_code: int
    content_type: str
    body: bytes
    peer_ip: str | None
    headers: dict[str, str]  # keys lower-cased


class ExternalSender(Protocol):
    """Performs exactly one HTTP request; injectable for deterministic tests."""

    async def __call__(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None,
        connect_ip: str,
        server_hostname: str,
        timeout_seconds: float,
        max_bytes: int,
    ) -> SentResponse: ...


def _redacted_brief(value: object) -> str:
    redacted = redact(str(value))
    return str(redacted)[:_MAX_ERROR_MESSAGE_CHARS]


class HttpxExternalSender:
    """httpx-backed sender: redirects disabled, streaming size cap, peer capture."""

    async def __call__(
        self,
        url: str,
        *,
        method: str,
        headers: dict[str, str],
        body: bytes | None,
        connect_ip: str,
        server_hostname: str,
        timeout_seconds: float,
        max_bytes: int,
    ) -> SentResponse:
        try:
            timeout = httpx.Timeout(timeout_seconds)
            original = httpx.URL(url)
            host_literal = f"[{connect_ip}]" if ":" in connect_ip else connect_ip
            connect_url = original.copy_with(host=host_literal)
            request_headers = dict(headers)
            request_headers["Host"] = server_hostname
            async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
                async with client.stream(
                    method,
                    connect_url,
                    headers=request_headers,
                    content=body if body else None,
                    extensions={"sni_hostname": server_hostname.encode("ascii")},
                ) as response:
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise ExternalMCPOversizedResponseError(
                                f"Response exceeds {max_bytes} bytes"
                            )
                        chunks.append(chunk)
                    peer_ip: str | None = None
                    network_stream = response.extensions.get("network_stream")
                    if network_stream is not None:
                        try:
                            peername = network_stream.get_extra_info("peername")
                        except Exception:  # noqa: BLE001 - peer info is best effort here
                            peername = None
                        if isinstance(peername, tuple) and peername:
                            peer_ip = str(peername[0])
                    lower_headers = {
                        key.casefold(): value for key, value in response.headers.items()
                    }
                    return SentResponse(
                        status_code=response.status_code,
                        content_type=lower_headers.get("content-type", ""),
                        body=b"".join(chunks),
                        peer_ip=peer_ip,
                        headers=lower_headers,
                    )
        except httpx.TimeoutException as exc:
            raise ExternalMCPTimeoutError(
                f"Request to external MCP host timed out after {timeout_seconds}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExternalMCPTransportError(_redacted_brief(exc)) from exc


@dataclass(frozen=True, slots=True)
class ExternalMCPConfig:
    """Typed bounds for every outbound MCP call."""

    request_timeout_seconds: float = 10.0
    max_response_bytes: int = 262_144
    max_redirects: int = 3


class ExternalMCPClient:
    """JSON-RPC client for allowlisted HTTPS MCP endpoints."""

    def __init__(
        self,
        policy: ExternalURLPolicy,
        sender: ExternalSender | None = None,
        config: ExternalMCPConfig | None = None,
    ) -> None:
        self._policy = policy
        self._sender: ExternalSender = sender or HttpxExternalSender()
        self._config = config or ExternalMCPConfig()
        self._ids = itertools.count(1)

    # ------------------------------------------------------------- public API

    async def get_json(self, endpoint_url: str) -> dict[str, Any]:
        """GETs and strictly validates a plain JSON document (no JSON-RPC wrap)."""
        return await self._request("GET", endpoint_url, None, jsonrpc_envelope=False)

    async def list_tools(
        self, endpoint_url: str, *, auth_header_env: str | None = None
    ) -> list[dict[str, Any]]:
        request_id = next(self._ids)
        result = await self._request(
            "POST",
            endpoint_url,
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/list",
                    "params": {},
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            auth_header_env=auth_header_env,
            expected_id=request_id,
        )
        tools = result.get("tools", [])
        if not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
            raise ExternalMCPBadResponseError("tools/list returned a malformed tools array")
        return cast(list[dict[str, Any]], tools)

    async def call_tool(
        self,
        endpoint_url: str,
        name: str,
        arguments: dict[str, Any],
        *,
        auth_header_env: str | None = None,
    ) -> dict[str, Any]:
        request_id = next(self._ids)
        raw_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        result = await self._request(
            "POST",
            endpoint_url,
            raw_body,
            auth_header_env=auth_header_env,
            expected_id=request_id,
        )
        if "content" in result and not isinstance(result["content"], list):
            raise ExternalMCPBadResponseError("tools/call content must be an array")
        return result

    # ------------------------------------------------------------ internals

    @staticmethod
    def _auth_headers(auth_header_env: str | None) -> dict[str, str]:
        """Reads bearer credentials from the referenced env var at call time.

        Only the variable *name* is persisted/configured; its value never
        reaches persistence, audit records or log output.
        """
        if not auth_header_env or not auth_header_env.strip():
            return {}
        env_name = auth_header_env.strip()
        value = os.environ.get(env_name, "").strip()
        if not value:
            logger.warning(
                "external_mcp_auth_env_unset",
                extra={"context": {"env_var": env_name}},
            )
            return {}
        return {"Authorization": f"Bearer {value}"}

    def _check_content_type(self, content_type: str) -> None:
        media_type = content_type.split(";")[0].strip().casefold()
        if media_type != "application/json":
            raise ExternalMCPBadResponseError(
                f"Unexpected response content type '{media_type}'"
            )

    async def _request(
        self,
        method: str,
        endpoint_url: str,
        raw_body: bytes | None,
        *,
        auth_header_env: str | None = None,
        jsonrpc_envelope: bool = True,
        expected_id: int | None = None,
    ) -> dict[str, Any]:
        current_url = endpoint_url
        allowed_ips: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] = ()

        for _hop in range(self._config.max_redirects + 1):
            # Static policy + fresh DNS validation for THIS hop only.
            target, allowed_ips = self._policy.validate_target_for_request(current_url)
            request_url = (
                f"https://{target.host}:{target.port}{target.path_with_query}"
            )
            headers = {"Accept": "application/json"}
            if raw_body is not None:
                headers["Content-Type"] = "application/json"
            headers.update(self._auth_headers(auth_header_env))

            try:
                response = await asyncio.wait_for(
                    self._sender(
                        request_url,
                        method=method,
                        headers=headers,
                        body=raw_body,
                        connect_ip=str(allowed_ips[0]),
                        server_hostname=target.host,
                        timeout_seconds=self._config.request_timeout_seconds,
                        max_bytes=self._config.max_response_bytes,
                    ),
                    timeout=self._config.request_timeout_seconds,
                )
            except TimeoutError as exc:
                raise ExternalMCPTimeoutError(
                    f"External MCP request timed out after "
                    f"{self._config.request_timeout_seconds}s"
                ) from exc
            except httpx.HTTPError as exc:
                raise ExternalMCPTransportError(_redacted_brief(exc)) from exc

            # DNS-rebinding defense: connected peer must match validated DNS.
            self._policy.assert_peer_allowed(response.peer_ip, allowed_ips)

            logger.debug(
                "external_mcp_hop_completed",
                extra={
                    "context": {
                        "host": target.host,
                        "status": response.status_code,
                        "bytes": len(response.body),
                    }
                },
            )

            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location", "").strip()
                if not location:
                    raise ExternalMCPBadResponseError("Redirect without Location header")
                current_url = urljoin(current_url, location)
                continue

            if not 200 <= response.status_code < 300:
                raise ExternalMCPBadResponseError(
                    f"Unexpected HTTP status {response.status_code}"
                )

            self._check_content_type(response.content_type)
            if not response.body:
                raise ExternalMCPBadResponseError("Empty response body")
            if len(response.body) > self._config.max_response_bytes:
                raise ExternalMCPOversizedResponseError(
                    f"Response exceeds {self._config.max_response_bytes} bytes"
                )
            try:
                parsed = json.loads(response.body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ExternalMCPBadResponseError("Malformed JSON response") from exc
            if not isinstance(parsed, dict):
                raise ExternalMCPBadResponseError("JSON response must be an object")

            if not jsonrpc_envelope:
                return cast(dict[str, Any], parsed)

            if parsed.get("jsonrpc") != "2.0":
                raise ExternalMCPBadResponseError("JSON-RPC response has invalid version")
            if parsed.get("id") != expected_id:
                raise ExternalMCPBadResponseError("JSON-RPC response id does not match request")

            error = parsed.get("error")
            if isinstance(error, dict):
                raw_message = error.get("message", "remote error")
                raw_code = error.get("code")
                code = raw_code if isinstance(raw_code, int) else 0
                raise ExternalMCPRemoteError(_redacted_brief(raw_message), code)
            if "result" not in parsed:
                raise ExternalMCPBadResponseError("JSON-RPC response missing result")
            result = parsed["result"]
            if not isinstance(result, dict):
                raise ExternalMCPBadResponseError("JSON-RPC result must be an object")
            return result

        raise ExternalMCPBadResponseError(
            f"Too many redirects (limit {self._config.max_redirects})"
        )
