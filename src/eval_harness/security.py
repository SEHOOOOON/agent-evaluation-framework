from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|token|secret|password|authorization|webhook(?:_url)?)",
    re.IGNORECASE,
)

INLINE_SECRET = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password|authorization|webhook(?:_url)?)\s*[:=]\s*)"
    r"([^\s,;]+)"
)


def redact_text(value: str) -> str:
    return INLINE_SECRET.sub(r"\1***REDACTED***", value)


def redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if SENSITIVE_KEY.search(str(key)) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_child(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if resolved_candidate != resolved_root and resolved_root not in resolved_candidate.parents:
        raise ValueError("허용된 실행 디렉터리 밖의 경로입니다.")
    return resolved_candidate

