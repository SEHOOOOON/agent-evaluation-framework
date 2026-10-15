"""Rule-based answer key for temperature range-change requests (A2 test design).

The key is written from the request/SRS by a person; this module only expands it
with the human-confirmed rules of 2026-10-15 (T-1/T-2/T-3/T-4/T-5/T-7/T-8/C-3)
and walks the A2 ``execution_spec`` to see which required tests exist. It reads
operations and typed verifications only; prose is never parsed for values.
Host-enforced target identity (C-9) is left to P1 and is not re-checked here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = "range-oracle-1.0"
LOCATIONS = ("panel", "card", "internal")
# Block verdicts are value retention only (T-8); these readers judge something else.
NON_VALUE_TARGETS = {"notification.toast", "temperature.controls", "temperature.increase",
                     "temperature.decrease", "temperature.display"}


class ModeRange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: str = Field(pattern=r"^[A-Z]+$")
    lower: int
    upper: int

    @model_validator(mode="after")
    def ordered(self):
        if self.lower >= self.upper:
            raise ValueError("lower must be below upper")
        return self


class RequestedTest(BaseModel):
    """T-4: a transition the request itself names (e.g. prepare P, apply V)."""
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: str = Field(pattern=r"^[A-Z]+$")
    prepared: int
    applied: int
    expected: int


class RangeKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    status: Literal["DRAFT", "HUMAN_CONFIRMED"]
    basis: str = Field(min_length=1)
    changed: list[ModeRange] = Field(min_length=1)
    kept: list[ModeRange] = []
    requested_tests: list[RequestedTest] = []
    # T-5 applies only while the request/SRS leaves the switch behavior undefined.
    switch_behavior_defined: bool = False

    @model_validator(mode="after")
    def distinct_modes(self):
        modes = [r.mode for r in (*self.changed, *self.kept)]
        if len(modes) != len(set(modes)):
            raise ValueError("Each mode appears once")
        return self


def expectations(key: RangeKey) -> list[dict[str, Any]]:
    """Expand the key into the required/recommended test list."""
    rows = []
    def add(rule, level, kind, mode, attempted, expected, start=None):
        rows.append(dict(id=f"{rule}:{kind}:{mode}:{attempted}", rule=rule, level=level, kind=kind,
                         mode=mode, start=start, attempted=attempted, expected=expected))
    for r in key.changed:
        add("T-1", "REQUIRED", "APPLY", r.mode, r.lower, r.lower)
        add("T-1", "REQUIRED", "APPLY", r.mode, r.upper, r.upper)
        add("T-2", "REQUIRED", "BLOCK", r.mode, r.lower - 1, r.lower, r.lower)
        add("T-2", "REQUIRED", "BLOCK", r.mode, r.upper + 1, r.upper, r.upper)
    for r in key.kept:
        add("T-3", "RECOMMENDED", "BLOCK", r.mode, r.lower - 1, r.lower, r.lower)
        add("T-3", "RECOMMENDED", "BLOCK", r.mode, r.upper + 1, r.upper, r.upper)
    for t in key.requested_tests:
        add("T-4", "REQUIRED", "APPLY" if t.applied == t.expected else "BLOCK",
            t.mode, t.applied, t.expected, t.prepared)
    return rows


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(str(value).replace("°C", "").strip())
    except ValueError:
        return None


def _expected_temp(verification: dict) -> tuple[str, Any] | None:
    target = str(verification.get("target") or "")
    location, _, field = target.partition(".")
    if field != "setTemp" or location not in LOCATIONS:
        return None
    for key in ("expected_number", "expected_value"):
        if _number(verification.get(key)) is not None:
            return location, _number(verification[key])
    for item in verification.get("expected_fields") or []:
        if item.get("field_name") == "setTemp":
            return location, _number(item.get("expected_value"))
    return location, None


def walk_design(design: dict) -> dict[str, list[dict]]:
    """Follow each TC's operations in order and collect TEST probes/mode switches."""
    probes, switches = [], []
    changed_ranges = {}
    for tc in design.get("test_cases") or []:
        spec = tc.get("execution_spec") or {}
        ops = spec.get("operations") or []
        verifications = spec.get("verifications") or []
        mode = temp = None
        tc_probes = []
        for op in ops:
            target, phase = str(op.get("target") or ""), op.get("phase")
            if phase == "RESTORE" or target == "controller.restore":
                break
            if target.startswith("mode."):
                new_mode = target.split(".", 1)[1].upper()
                switches.append(dict(tc_id=tc.get("tc_id"), action_id=op.get("action_id"),
                                     phase=phase, from_mode=mode, temperature=temp, to_mode=new_mode))
                mode = new_mode
                continue
            attempted = None
            if target == "temperature.set":
                attempted = _number(op.get("value"))
                start = temp
            elif target in {"temperature.increase", "temperature.decrease"} and temp is not None:
                start = temp
                attempted = temp + (1 if target.endswith("increase") else -1)
            if attempted is None:
                if phase == "TEST" and tc_probes:
                    tc_probes[-1]["window"].append(op.get("action_id"))
                continue
            if phase == "TEST":
                tc_probes.append(dict(tc_id=tc.get("tc_id"), mode=mode, start=start, attempted=attempted,
                                      window=[op.get("action_id")]))
            temp = attempted
        for probe in tc_probes:
            seen, extras = {}, []
            for v in verifications:
                if v.get("after_action_id") not in probe["window"]:
                    continue
                found = _expected_temp(v)
                if found:
                    seen[found[0]] = found[1]
                elif str(v.get("target")) in NON_VALUE_TARGETS:
                    extras.append(dict(result_id=v.get("result_id"), target=v.get("target")))
            probe.update(expected=seen, non_value_checks=extras)
            del probe["window"]
        probes += tc_probes
    return {"probes": probes, "switches": switches}


def grade(key: RangeKey, design: dict) -> dict[str, Any]:
    walked = walk_design(design)
    probes = walked["probes"]
    items, issues = [], []
    for row in expectations(key):
        matches = [p for p in probes if p["mode"] == row["mode"] and p["attempted"] == row["attempted"]
                   and (row["start"] is None or p["start"] == row["start"])]
        status, detail = "MISSING", "TEST 단계에서 이 값을 시도하는 조작이 없음"
        for p in matches:
            values = p["expected"]
            wrong = {loc: v for loc, v in values.items() if v is not None and v != row["expected"]}
            missing = [loc for loc in LOCATIONS if values.get(loc) != row["expected"]]
            if wrong:
                status, detail = "WRONG_EXPECTATION", f"{p['tc_id']}: 기대값 {wrong}, 정답 {row['expected']}"
                break
            if not missing:
                status, detail = "MET", p["tc_id"]
                break
            status, detail = "PARTIAL", f"{p['tc_id']}: {', '.join(missing)} 확인 없음 (C-3)"
        if row["kind"] == "BLOCK":
            for p in matches:
                if p["non_value_checks"]:
                    issues.append(dict(rule="T-8", tc_id=p["tc_id"], detail=p["non_value_checks"],
                                       reason="차단 판정에 값 유지 외 확인을 기대결과로 사용"))
        items.append(dict(row, status=status, detail=detail))
    changed = {r.mode: r for r in key.changed}
    if not key.switch_behavior_defined:
        for s in walked["switches"]:
            r = changed.get(s["to_mode"])
            if r and s["temperature"] is not None and not r.lower <= s["temperature"] <= r.upper:
                asked = _asks_about(design, r.mode)
                items.append(dict(id=f"T-5:QUESTION:{r.mode}:{s['tc_id']}", rule="T-5", level="REQUIRED",
                                  kind="QUESTION", mode=r.mode, start=s["temperature"], attempted=None,
                                  expected=None, status="MET" if asked else "MISSING",
                                  detail=f"{s['tc_id']} {s['action_id']}: {s['from_mode']} {s['temperature']} → {r.mode}"))
    for mode in changed:
        boundary = {r["attempted"] for r in items if r["mode"] == mode and r["rule"] in {"T-1", "T-2"}}
        tcs = {p["tc_id"] for p in probes if p["mode"] == mode and p["attempted"] in boundary}
        if len(tcs) > 1:
            issues.append(dict(rule="T-7", tc_id=sorted(tcs), detail=mode,
                               reason="한 모드의 경계 시험이 여러 TC로 나뉨"))
    required = [i for i in items if i["level"] == "REQUIRED"]
    met = sum(i["status"] == "MET" for i in required)
    if met < len(required) or any(i["rule"] == "T-8" for i in issues):
        verdict = "FAIL"
    elif issues:
        verdict = "REVIEW"
    else:
        verdict = "PASS"
    return dict(version=VERSION, key_id=key.key_id, key_status=key.status, request_id=key.request_id,
                verdict=verdict, required_met=met, required_total=len(required),
                items=items, issues=issues,
                limitations=["실행 정의(operations/verifications)만 대조하며 문장 의미·제품 결과는 판정하지 않음",
                             "질문 여부는 확인 사항 문장에 모드 이름이 있는지로만 확인",
                             "대상 장비 일치(C-9)는 P1 실행기가 검사하므로 여기서 다시 보지 않음"])


MODE_WORDS = {"COOL": ("COOL", "냉방"), "HEAT": ("HEAT", "난방"), "AUTO": ("AUTO", "자동"),
              "DRY": ("DRY", "제습"), "FAN": ("FAN", "송풍")}


def _asks_about(design: dict, mode: str) -> bool:
    texts = []
    for field in ("최종_확인_사항", "중단_확인_사항"):
        for entry in design.get(field) or []:
            texts.append(json.dumps(entry, ensure_ascii=False) if not isinstance(entry, str) else entry)
    words = MODE_WORDS.get(mode, (mode,))
    return any(any(w in t for w in words) for t in texts)


def load_key(path: Path) -> RangeKey:
    return RangeKey.model_validate_json(path.read_text(encoding="utf-8"))
