"""Device Gateway package for Veetee WebSocket protocol."""

from .mcp_broker import (
    DeviceMCPBroker,
    DeviceMCPBrokerError,
    DeviceMCPCallResult,
    DeviceMCPDisconnectedError,
    DeviceMCPTimeoutError,
    DeviceMCPToolError,
)
from .registry import DeviceSessionRegistry
from .router import router

__all__ = [
    "DeviceMCPCallResult",
    "DeviceMCPBroker",
    "DeviceMCPBrokerError",
    "DeviceMCPDisconnectedError",
    "DeviceMCPTimeoutError",
    "DeviceMCPToolError",
    "DeviceSessionRegistry",
    "router",
]
