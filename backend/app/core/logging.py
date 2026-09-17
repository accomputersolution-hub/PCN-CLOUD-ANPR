from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import get_settings

SENSITIVE_KEYS = {"password", "rtsp_password", "username", "secret", "token", "authorization"}


def _drop_secrets(_: object, __: str, event_dict: dict) -> dict:
    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "***"
    return event_dict


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level.upper())
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _drop_secrets,
            structlog.dev.ConsoleRenderer() if settings.debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(settings.log_level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
