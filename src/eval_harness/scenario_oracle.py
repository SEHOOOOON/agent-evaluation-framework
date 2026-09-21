"""Explicit scenario contracts, not an LLM or natural-language quality grader."""
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rule: str = Field(pattern=r"^(A[1-4]|E2E)\.SCENARIO\.[A-Z0-9_]+$")
    artifact: str
    path: list[str] = Field(min_length=1)
    operator: Literal["equals", "contains", "count"]
    expected: Any
    reason: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    critical: bool = True

    @model_validator(mode="after")
    def validate_contract(self):
        from .v2_recorded import child
        from pathlib import Path
        child(Path.cwd(), self.artifact)
        if not self.artifact.endswith(".json"):
            raise ValueError("Scenario artifacts must be JSON")
        if self.operator == "count" and (type(self.expected) is not int or self.expected < 0):
            raise ValueError("Count requires a non-negative integer")
        return self


def select(document, path):
    """Traverse explicit keys; wildcard expands lists only. Missing is an error."""
    if not path:
        return [document]
    key, *rest = path
    if key == "*":
        if not isinstance(document, list):
            raise TypeError("Wildcard requires a list")
        return [value for item in document for value in select(item, rest)]
    if not isinstance(document, dict):
        raise TypeError("Key requires an object")
    return select(document[key], rest)


def subset(actual, expected):
    """Match fields within ONE object, preventing cross-row false positives."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(actual[k], v) for k, v in expected.items())
    # Strict JSON equality: True is not numeric 1, nor is 18 the string '18'.
    return json.dumps(actual, sort_keys=True, ensure_ascii=False) == json.dumps(expected, sort_keys=True, ensure_ascii=False)


def evaluate_expectations(run, expectations):
    from .v2_recorded import child, read
    checks = []
    for spec in expectations:
        try:
            values = select(read(child(run, spec.artifact)), spec.path)
            if spec.operator == "count":
                ok = len(values) == spec.expected
            elif spec.operator == "contains":
                ok = any(subset(value, spec.expected) for value in values)
            else:
                ok = len(values) == 1 and subset(values[0], spec.expected) and subset(spec.expected, values[0])
            status = "PASS" if ok else "FAIL"
            detail = ""
        except (OSError, ValueError, KeyError, TypeError) as exc:
            status, detail = "ERROR", " · 자료/경로 해석 실패: " + type(exc).__name__
        checks.append({"rule": spec.rule, "status": status, "critical": spec.critical,
                       "reason": spec.reason + " · 기준: " + spec.basis + detail})
    return checks
