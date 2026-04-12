from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger("sunbeat.ai_gateway")
SENSITIVE_LOG_KEYS = {
    "content",
    "text",
    "raw_response",
}


def _redact_sensitive_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if key in SENSITIVE_LOG_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_sensitive_log_value(item)
        return redacted

    if isinstance(value, list):
        return [_redact_sensitive_log_value(item) for item in value]

    return value


def sink_ai_log_payload(
    payload: Dict[str, Any],
    *,
    level: str = "info",
) -> Dict[str, str]:
    serialized_payload = json.dumps(
        _redact_sensitive_log_value(payload),
        ensure_ascii=True,
        default=str,
        sort_keys=True,
    )

    normalized_level = str(level or "info").strip().lower()

    if normalized_level == "error":
        logger.error(serialized_payload)
    elif normalized_level == "warning":
        logger.warning(serialized_payload)
    else:
        logger.info(serialized_payload)

    return {
        "sink": "python_logger",
        "logger_name": logger.name,
        "level": normalized_level,
    }
