"""Structured JSON logging для L0.

Принципы:
  - Каждый запуск CLI / API call имеет correlation_id (UUID v4)
  - Лог идёт в stderr (stdout зарезервирован для verify --json output)
  - Format: JSON per line — легко парсить через jq / loki / splunk
  - В тестах logging не интрузивен (level WARN by default; configure через ENV)

ENV:
  CAMERA_BRINGUP_LOG_LEVEL    = DEBUG | INFO | WARNING | ERROR (default INFO)
  CAMERA_BRINGUP_LOG_FORMAT   = json | human (default json)
  CAMERA_BRINGUP_TRACE_ID     = override correlation ID (для chain'инга с upstream)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from typing import Any

# Один correlation ID на весь процесс (CLI invocation = one trace)
TRACE_ID: str = os.environ.get("CAMERA_BRINGUP_TRACE_ID") or uuid.uuid4().hex[:16]


class JsonFormatter(logging.Formatter):
    """Один log record = одна JSON строка."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": TRACE_ID,
            "msg": record.getMessage(),
        }
        # Extra fields, переданные через extra={'foo':'bar'}
        for k, v in record.__dict__.items():
            if k in ("name", "msg", "args", "levelname", "levelno", "pathname",
                     "filename", "module", "exc_info", "exc_text", "stack_info",
                     "lineno", "funcName", "created", "msecs", "relativeCreated",
                     "thread", "threadName", "processName", "process", "message",
                     "taskName"):
                continue
            try:
                json.dumps(v)  # serializable?
                payload[k] = v
            except (TypeError, ValueError):
                payload[k] = repr(v)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Read-friendly формат для интерактивной диагностики."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.gmtime(record.created))
        return f"{ts} [{record.levelname[:4]}] {record.name}: {record.getMessage()}"


_configured = False


def configure() -> None:
    """Настроить root logger. Idempotent."""
    global _configured
    if _configured:
        return

    level_name = os.environ.get("CAMERA_BRINGUP_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt_name = os.environ.get("CAMERA_BRINGUP_LOG_FORMAT", "json").lower()
    formatter = JsonFormatter() if fmt_name == "json" else HumanFormatter()

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger("camera_bringup")
    root.setLevel(level)
    # Очищаем дефолтные handlers (чтобы не было дубликата при repeated configure())
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False   # не дублировать в python root logger

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Получить named logger в нашем namespace."""
    if not _configured:
        configure()
    # All loggers будут под camera_bringup.* prefix
    if not name.startswith("camera_bringup"):
        name = f"camera_bringup.{name}"
    return logging.getLogger(name)
