from __future__ import annotations

from typing import Any, Protocol


class VpnStatusReport(Protocol):
    connected: bool
    detail: str


class VpnProvider(Protocol):
    """Vendor-agnostic VPN plugin. Do not import ER605 or MikroTik APIs in core."""

    name: str

    def connect(self) -> None:
        """Establish outbound VPN. Must not require inbound public ports."""

    def disconnect(self) -> None:
        ...

    def status(self) -> VpnStatusReport:
        ...


class LanReporter(Protocol):
    def snapshot(self) -> dict[str, Any]:
        """LAN subnet, optional NVR ping results. No credentials."""


class GatewayAgent(Protocol):
    def authenticate(self, gateway_id: str, device_key: str) -> None:
        ...

    def heartbeat_loop(self) -> None:
        """POST /api/v1/gateways/{id}/heartbeat with X-Gateway-Id / X-Gateway-Key."""

    def reconnect(self) -> None:
        """WAN loss recovery: VPN then heartbeat."""
