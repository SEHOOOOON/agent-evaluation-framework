"""Rule-based answer key for temperature range-change requests (A2 test design).

The key is written from the request/SRS by a person; this module only expands it
with the human-confirmed rules of 2026-10-15 (T-1/T-2/T-3/T-4/T-5/T-7/T-8/C-3)
and walks the A2 ``execution_spec`` to see which required tests exist. It reads
operations and typed verifications only; prose is never parsed for values.
Host-enforced target identity (C-9) is left to P1 and is not re-checked here.

1.3 (2026-10-17 사람 대조 H-2): when the request itself lists the switch behavior
as out of scope (``switch_behavior_excluded``), the T-5 question is OPTIONAL and
not counted as required; asking anyway is not penalized. 1.3 also reads the
English design field names (final_review_notes, human_review_notes).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = "range-oracle-1.4"  # 1.4 (2026-10-25): start after a blocked attempt, any correct probe passes
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
    """T-4: a transition the request itself names (e.g. prepare P, apply V).

    prepared=None means the request fixes only the applied value (any start).
    """
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: str = Field(pattern=r"^[A-Z]+$")
    prepared: int | None = None
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
    # H-2: the request's out_of_scope names this switch policy -> question optional.
    switch_behavior_excluded: bool = False

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


def _settled(spec: dict, window: list) -> float | None:
    """setTemp the TC expects right after the latest TEST attempt (internal value first).

    2026-10-25 review: after a blocked attempt (18 -> 17 refused) the next button press
    starts from the kept 18, not from the attempted 17.
    """
    found = {}
    for v in spec.get("verifications") or []:
        if v.get("after_action_id") in window:
            hit = _expected_temp(v)
            if hit and hit[1] is not None:
                found.setdefault(hit[0], hit[1])
    for location in ("internal", "panel", "card"):
        if location in found:
            return found[location]
    return None


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
                settled = _settled(spec, tc_probes[-1]["window"]) if tc_probes else None
                start = temp if settled is None else settled
                attempted = start + (1 if target.endswith("increase") else -1)
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


def _out_of_range(changed: ModeRange, kept: list[ModeRange]) -> list[ModeRange]:
    """Kept modes that allow values outside the changed mode's new range."""
    return [k for k in kept if k.lower < changed.lower or k.upper > changed.upper]


def grade(key: RangeKey, design: dict, analysis: dict | None = None) -> dict[str, Any]:
    """``analysis`` (optional A1 output) adds its questions/gaps to the T-5 check."""
    walked = walk_design(design)
    probes = walked["probes"]
    items, issues = [], []
    for row in expectations(key):
        matches = [p for p in probes if p["mode"] == row["mode"] and p["attempted"] == row["attempted"]
                   and (row["start"] is None or p["start"] == row["start"])]
        status, detail = "MISSING", "TEST 단계에서 이 값을 시도하는 조작이 없음"
        # Any correct probe meets the row; otherwise a wrong value is reported before a gap
        # (1.4: the first matching TC no longer decides alone).
        found = []
        for p in matches:
            values = p["expected"]
            wrong = {loc: v for loc, v in values.items() if v is not None and v != row["expected"]}
            missing = [loc for loc in LOCATIONS if values.get(loc) != row["expected"]]
            if not wrong and not missing:
                found = [("MET", p["tc_id"])]
                break
            if wrong:
                found.append(("WRONG_EXPECTATION", f"{p['tc_id']}: 기대값 {wrong}, 정답 {row['expected']}"))
            else:
                found.append(("PARTIAL", f"{p['tc_id']}: {', '.join(missing)} 확인 없음 (C-3)"))
        if found:
            order = {"MET": 0, "WRONG_EXPECTATION": 1, "PARTIAL": 2}
            status, detail = min(found, key=lambda item: order[item[0]])
        if status == "MET":
            # The row is met, but a wrong expectation for the same attempt elsewhere is kept.
            for p in matches:
                wrong = {loc: v for loc, v in p["expected"].items() if v is not None and v != row["expected"]}
                if wrong:
                    issues.append(dict(rule="WRONG_OTHER_TC", tc_id=p["tc_id"], detail=row["id"],
                                       reason=f"같은 시도를 다른 TC가 맞게 확인했지만 이 TC의 기대값 {wrong}은 정답 {row['expected']}와 다름"))
        if row["kind"] == "BLOCK":
            for p in matches:
                if p["non_value_checks"]:
                    issues.append(dict(rule="T-8", tc_id=p["tc_id"], detail=p["non_value_checks"],
                                       reason="차단 판정에 값 유지 외 확인을 기대결과로 사용"))
        items.append(dict(row, status=status, detail=detail))
    changed = {r.mode: r for r in key.changed}
    if not key.switch_behavior_defined:
        # T-5 follows the request, not the TC: a person asked for the question even
        # when no TC exercises the switch (2026-10-16, option 1).
        for r in key.changed:
            sources = _out_of_range(r, key.kept)
            if not sources:
                continue
            seen = [f"{s['tc_id']} {s['action_id']}: {s['from_mode']} {s['temperature']} → {r.mode}"
                    for s in walked["switches"] if s["to_mode"] == r.mode and s["temperature"] is not None
                    and not r.lower <= s["temperature"] <= r.upper]
            asked = _asks_about(design, r.mode, analysis, [k.mode for k in sources])
            ranges = ", ".join(f"{k.mode} {k.lower}~{k.upper}" for k in sources)
            status = {"SWITCH": "MET", "MODE_ONLY": "PARTIAL"}.get(asked, "MISSING")
            note = " / 확인 문장에 모드 이름만 있고 유지 모드·전환·범위 밖 표현이 없음" if asked == "MODE_ONLY" else ""
            level = "OPTIONAL" if key.switch_behavior_excluded else "REQUIRED"
            if key.switch_behavior_excluded:
                note += " / 요청서가 이 전환 정책을 '이번에 다루지 않음'으로 제외해 질문은 선택(H-2)"
            items.append(dict(id=f"T-5:QUESTION:{r.mode}", rule="T-5", level=level, kind="QUESTION",
                              mode=r.mode, start=None, attempted=None, expected=None, status=status,
                              detail=f"{ranges}에는 {r.mode} {r.lower}~{r.upper} 밖 값이 있음; 전환 동작 미정"
                                     + (f" (TC 전환: {'; '.join(seen)})" if seen else " (TC에 해당 전환 없음)")
                                     + note))
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
                             "질문 여부는 A2 확인 사항(및 주어진 경우 A1 질문·정보 부족)의 한 문장에 바뀐 모드와 유지 모드(또는 전환·범위 밖 표현)가 함께 있는지로만 확인하며 문장 의미는 판정하지 않음",
                             "대상 장비 일치(C-9)는 P1 실행기가 검사하므로 여기서 다시 보지 않음"])


MODE_WORDS = {"COOL": ("COOL", "냉방"), "HEAT": ("HEAT", "난방"), "AUTO": ("AUTO", "자동"),
              "DRY": ("DRY", "제습"), "FAN": ("FAN", "송풍")}


# Words that tie a question to the switch itself rather than to the mode in general.
SWITCH_WORDS = ("전환", "바꾸", "바꿀", "바뀌", "바뀔", "범위 밖", "범위를 벗어",
                "switch", "out of range", "outside")


def _asks_about(design: dict, mode: str, analysis: dict | None = None,
                kept_modes: list[str] | tuple = ()) -> str | None:
    """Return SWITCH when one entry names the changed mode together with a kept
    mode or a switch/out-of-range phrase, MODE_ONLY when entries only name the
    mode, None otherwise. Words in different entries are never combined."""
    texts = []
    # P1 saves Korean display names; English field names are read too (1.3).
    sources = [(design, ("최종_확인_사항", "중단_확인_사항", "final_review_notes", "human_review_notes"))]
    if analysis:
        sources.append((analysis, ("user_questions", "information_gaps")))
    for doc, fields in sources:
        for field in fields:
            for entry in doc.get(field) or []:
                # Values only: field names such as "switch_id" must not count as words.
                texts.append(" ".join(_strings(entry)))
    words = MODE_WORDS.get(mode, (mode,))
    other = [w for k in kept_modes for w in MODE_WORDS.get(k, (k,))]
    found = None
    for t in texts:
        if not any(w in t for w in words):
            continue
        if any(w in t for w in other) or any(w in t.lower() for w in SWITCH_WORDS):
            return "SWITCH"
        found = "MODE_ONLY"
    return found


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in _strings(v)]
    if isinstance(value, list):
        return [t for v in value for t in _strings(v)]
    return []


def load_key(path: Path) -> RangeKey:
    return RangeKey.model_validate_json(path.read_text(encoding="utf-8"))
