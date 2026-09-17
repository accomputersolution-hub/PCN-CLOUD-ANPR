from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from pcn_edge.config import EdgeSettings


@dataclass
class ReconnectState:
    attempts: int = 0
    last_error: str | None = None
    connected: bool = False


class CameraReconnect:
    """Exponential backoff reconnect for RTSP/ONVIF streams."""

    def __init__(self, settings: EdgeSettings) -> None:
        self.settings = settings
        self.state = ReconnectState()

    def next_delay(self) -> float:
        delay = min(
            self.settings.reconnect_max_seconds,
            self.settings.reconnect_initial_seconds * (2 ** self.state.attempts),
        )
        jitter = delay * 0.1 * random.random()
        return delay + jitter

    def record_failure(self, error: str) -> float:
        self.state.attempts += 1
        self.state.connected = False
        self.state.last_error = error
        return self.next_delay()

    def record_success(self) -> None:
        self.state.attempts = 0
        self.state.connected = True
        self.state.last_error = None

    def run(self, connect: Callable[[], None], should_stop: Callable[[], bool] | None = None) -> None:
        while True:
            if should_stop and should_stop():
                return
            try:
                connect()
                self.record_success()
                return
            except Exception as exc:  # noqa: BLE001 — reconnect must swallow stream errors
                delay = self.record_failure(str(exc))
                time.sleep(delay)
