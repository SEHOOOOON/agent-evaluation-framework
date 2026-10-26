"""Option B (user decision 2026-10-16): declare expected product mismatches.

The product under test is the unchanged baseline, so a correct TC for a change
request FAILS on it (e.g. COOL 20 + decrease shows 19). Before a run, a person
declares which mismatches must appear. After the run, verified assertion records
are compared with that list:

- AS_EXPECTED: every declared mismatch appeared, and no other mismatch did.
- NOT_AS_EXPECTED: a declared mismatch is absent (the check is missing, or it
  passed), or an undeclared mismatch appeared.
- UNVERIFIED: the assertion records did not fully verify (A3-03 not PASS).
- NOT_DECLARED: the spec lists nothing; nothing is inferred.

This is a separate result, not a score and not a change to COMPLETED/STOPPED.
Values come from the declaration only; no mode or scenario names are coded here.
"""
from __future__ import annotations

from typing import Union

from pydantic import BaseModel, ConfigDict, Field

from .execution_observations import equal

VERSION = "product-expectation-1.0"
Scalar = Union[bool, int, float, str]


class ExpectedMismatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(pattern=r"^[A-Za-z]{1,40}$")
    expected: Scalar
    actual: Scalar
    reason: str = Field(min_length=1, max_length=300)


def mismatches(event: dict) -> list[tuple[str, object, object]]:
    """(field, expected, actual) for each unmatched value in one record."""
    if event.get("matched") is not False:
        return []
    assertion, actual = event["assertion"], event.get("actual")
    strategy = assertion.get("strategy")
    if strategy in {"UI_TEMPERATURE", "INTERNAL_SET_TEMP"}:
        return [("setTemp", assertion.get("expected_number"), actual)]
    if strategy in {"CONTROLLER_UI_FIELDS_EQUALS", "INTERNAL_DEVICE_FIELDS_EQUALS"}:
        found = actual if isinstance(actual, dict) else {}
        return [(f["field_name"], f["expected_value"], found.get(f["field_name"]))
                for f in assertion.get("expected_fields") or []
                if not equal(found.get(f["field_name"]), f["expected_value"])]
    # Other readers (toast, enabled...) cannot be declared by field; keep them visible.
    return [(str(strategy), None, actual)]


def classify(declared: list[ExpectedMismatch], observations: dict[str, list[dict]],
             observation_verdict: str) -> dict:
    if not declared:
        return dict(version=VERSION, status="NOT_DECLARED")
    if observation_verdict != "PASS":
        return dict(version=VERSION, status="UNVERIFIED",
                    reason="시험 관찰 기록이 완전히 검증되지 않아(A3-03 " + str(observation_verdict) + ") 대조하지 않음")
    seen = [dict(tc_id=tc_id, result_id=e["assertion"].get("result_id"), field=f, expected=x, actual=a)
            for tc_id, events in sorted(observations.items()) for e in events for f, x, a in mismatches(e)]
    covered, items = set(), []
    for d in declared:
        hits = [i for i, m in enumerate(seen) if m["field"] == d.field
                and equal(m["expected"], d.expected) and equal(m["actual"], d.actual)]
        covered.update(hits)
        items.append(dict(d.model_dump(), status="OBSERVED" if hits else "NOT_OBSERVED",
                          where=[f"{seen[i]['tc_id']}/{seen[i]['result_id']}" for i in hits]))
    unexpected = [m for i, m in enumerate(seen) if i not in covered]
    ok = all(i["status"] == "OBSERVED" for i in items) and not unexpected
    return dict(version=VERSION, status="AS_EXPECTED" if ok else "NOT_AS_EXPECTED",
                declared=items, unexpected=unexpected,
                note="NOT_OBSERVED는 해당 확인이 TC에 없거나 제품이 통과한 경우입니다. 둘을 구분하지 않습니다.")
