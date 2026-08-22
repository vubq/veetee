"""Security and schema tests for M6.6 external integrations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from veetee_server.tools.external_mcp import (
    ExternalMCPBadResponseError,
    ExternalMCPClient,
    ExternalMCPConfig,
    ExternalMCPOversizedResponseError,
    ExternalMCPTimeoutError,
    SentResponse,
)
from veetee_server.tools.integrations import (
    IntegrationGate,
    IntegrationPermissionError,
    IntegrationPermissionSnapshot,
    IntegrationRateLimitError,
    SlidingWindowRateLimiter,
)
from veetee_server.tools.ssrf import ExternalURLPolicy, ExternalUrlPolicyError
from veetee_server.tools.weather import OpenMeteoWeatherTool, WeatherLookupError


class FakeSender:
    def __init__(self, responses: list[SentResponse] | None = None, delay: float = 0) -> None:
        self.responses = list(responses or [])
        self.delay = delay
        self.requests: list[dict[str, Any]] = []

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
        self.requests.append(
            {
                "url": url,
                "method": method,
                "headers": headers,
                "body": body,
                "connect_ip": connect_ip,
                "server_hostname": server_hostname,
            }
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.responses.pop(0)


def _json_response(payload: dict[str, Any], *, peer: str = "93.184.216.34") -> SentResponse:
    return SentResponse(
        status_code=200,
        content_type="application/json",
        body=json.dumps(payload).encode(),
        peer_ip=peer,
        headers={"content-type": "application/json"},
    )


def _policy(
    hosts: list[str], resolver: Callable[[str], list[str]] | None = None
) -> ExternalURLPolicy:
    return ExternalURLPolicy(hosts, resolver or (lambda _host: ["93.184.216.34"]))


def test_url_policy_denies_non_https_userinfo_private_dns_and_peer_mismatch() -> None:
    policy = _policy(["mcp.example.test"])
    for url in (
        "http://mcp.example.test/rpc",
        "https://user:pass@mcp.example.test/rpc",
        "https://other.example.test/rpc",
    ):
        with pytest.raises(ExternalUrlPolicyError):
            policy.validate_url(url)
    with pytest.raises(ExternalUrlPolicyError):
        _policy(["mcp.example.test"], lambda _host: ["127.0.0.1"]).validate_target_for_request(
            "https://mcp.example.test/rpc"
        )
    with pytest.raises(ExternalUrlPolicyError):
        policy.assert_peer_allowed("8.8.8.8", policy.resolve_validated_ips("mcp.example.test"))


@pytest.mark.asyncio
async def test_external_client_validates_redirect_peer_timeout_size_and_jsonrpc() -> None:
    redirect_sender = FakeSender(
        [
            SentResponse(302, "", b"", "93.184.216.34", {"location": "https://evil.test/x"})
        ]
    )
    client = ExternalMCPClient(_policy(["mcp.example.test"]), redirect_sender)
    with pytest.raises(ExternalUrlPolicyError):
        await client.list_tools("https://mcp.example.test/rpc")

    peer_sender = FakeSender(
        [_json_response({"jsonrpc": "2.0", "id": 1, "result": {}}, peer="8.8.8.8")]
    )
    with pytest.raises(ExternalUrlPolicyError):
        await ExternalMCPClient(_policy(["mcp.example.test"]), peer_sender).list_tools(
            "https://mcp.example.test/rpc"
        )

    slow = ExternalMCPClient(
        _policy(["mcp.example.test"]),
        FakeSender([_json_response({})], delay=0.05),
        ExternalMCPConfig(request_timeout_seconds=0.001),
    )
    with pytest.raises(ExternalMCPTimeoutError):
        await slow.list_tools("https://mcp.example.test/rpc")

    oversized = FakeSender(
        [SentResponse(200, "application/json", b"x" * 33, "93.184.216.34", {})]
    )
    with pytest.raises(ExternalMCPOversizedResponseError):
        await ExternalMCPClient(
            _policy(["mcp.example.test"]),
            oversized,
            ExternalMCPConfig(max_response_bytes=32),
        ).list_tools("https://mcp.example.test/rpc")

    invalid = FakeSender(
        [_json_response({"jsonrpc": "1.0", "id": 1, "result": {"tools": []}})]
    )
    with pytest.raises(ExternalMCPBadResponseError):
        await ExternalMCPClient(_policy(["mcp.example.test"]), invalid).list_tools(
            "https://mcp.example.test/rpc"
        )


@pytest.mark.asyncio
async def test_get_json_is_get_and_weather_payload_is_strictly_bounded() -> None:
    sender = FakeSender(
        [
            _json_response({"results": [{"latitude": 10.8, "longitude": 106.6, "name": "TP.HCM"}]}),
            _json_response(
                {
                    "current": {
                        "temperature_2m": 31.5,
                        "relative_humidity_2m": 70,
                        "weather_code": 2,
                        "wind_speed_10m": 8.0,
                    }
                }
            ),
        ]
    )
    client = ExternalMCPClient(
        _policy(["geocoding-api.open-meteo.com", "api.open-meteo.com"]), sender
    )
    result = await OpenMeteoWeatherTool(client).lookup("Hồ Chí Minh")
    assert result["condition"] == "Có mây rải rác"
    assert [item["method"] for item in sender.requests] == ["GET", "GET"]
    assert [item["connect_ip"] for item in sender.requests] == [
        "93.184.216.34",
        "93.184.216.34",
    ]
    assert [item["server_hostname"] for item in sender.requests] == [
        "geocoding-api.open-meteo.com",
        "api.open-meteo.com",
    ]

    invalid = FakeSender(
        [_json_response({"results": [{"latitude": True, "longitude": 106.6, "name": "X"}]})]
    )
    with pytest.raises(WeatherLookupError):
        await OpenMeteoWeatherTool(
            ExternalMCPClient(
                _policy(["geocoding-api.open-meteo.com", "api.open-meteo.com"]), invalid
            )
        ).lookup("X")


def test_integration_gate_is_default_deny_and_rate_limited_per_agent() -> None:
    with pytest.raises(IntegrationPermissionError):
        IntegrationGate().authorize("owner", "agent", "endpoint", "call")
    now = [1.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: now[0])
    gate = IntegrationGate(
        lambda _owner, _agent, _endpoint: IntegrationPermissionSnapshot(
            can_list=True, can_call=True, rate_limit_calls=1, rate_limit_window_seconds=10
        ),
        limiter,
    )
    gate.authorize("owner", "agent-a", "endpoint", "call")
    with pytest.raises(IntegrationRateLimitError):
        gate.authorize("owner", "agent-a", "endpoint", "call")
    gate.authorize("owner", "agent-b", "endpoint", "call")
