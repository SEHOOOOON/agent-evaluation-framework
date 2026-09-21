"""Role-specific rubric and conservative coverage reporting; no model calls."""
from fnmatch import fnmatchcase
from html import escape

VERSION = "agent-rubric-1.1"
# owner, ID, objective, automatic evidence patterns, semantic review needed, critical risk
# Korean labels are deliberately kept separate from execution rule IDs.
CRITERIA = [
    ("COMMON", "C01", "입력·출력 파일 무결성", ("CHAIN.*", "A4.CHAIN"), False, True),
    ("COMMON", "C02", "역할·권한 준수", (), True, True),
    ("A1", "A1-01", "변경·유지 조건과 값·상태의 의미 보존", (), True, True),
    ("A1", "A1-02", "확정 정보와 정보 부족 구분", (), True, True),
    ("A1", "A1-03", "영향 범위 누락·과잉 확장 방지", (), True, False),
    ("A1", "A1-04", "분석 조건의 근거 추적", ("A1.CONDITION_IDS", "A1.SOURCE_QUOTES"), True, True),
    ("A2", "A2-01", "기존 TC 재사용 적절성과 중복 방지", ("A2.UNIQUE_TC",), True, False),
    ("A2", "A2-02", "확정 조건·경계·예외 검증 범위", (), True, False),
    ("A2", "A2-03", "사용자 정답지 수준의 조작·기대결과 상세도", ("A2.*.DETAIL", "A2.*.TIMING"), True, False),
    ("A2", "A2-04", "기대값의 요구사항 근거", ("A2.*.TRACE",), True, True),
    ("A2", "A2-05", "독립 실행·복원 성공 확인", (), True, False),
    ("A3", "A3-01", "AI 계획의 대상·조작·기대값·판정 시점 보존", ("EXEC.DESIGN_LINK",), True, True),
    ("A3", "A3-02", "UI 요소의 적절성과 지원 행동 판단", (), True, True),
    ("A3", "A3-03", "컴파일러의 계획 보존과 코드 유효성", ("EXEC.*.CODE_HASH", "EXEC.*.COMPILE"), True, True),
    ("A3", "A3-04", "실행 결과·복원·필수 증거의 충분성", ("EXEC.*.EXIT", "EXEC.*.EVIDENCE"), True, True),
    ("A4", "A4-01", "원본 실행 행의 집계 정확성", ("EXEC.UNIQUE_ROWS", "A4.COUNTS", "A4.ORACLE_COUNTS"), False, True),
    ("A4", "A4-02", "중립 증거에 따른 원인 분류 정확성", ("A4.FINDINGS",), True, True),
    ("A4", "A4-03", "분류 대상·상태·증거의 연결", ("A4.FINDING_LINK",), False, True),
    ("A4", "A4-04", "결함 단정 방지와 권고·사람 승인 구분", ("A4.RECOMMENDATION",), True, True),
    ("E2E", "E01", "조건부터 실제 시험까지 전체 추적·완료 범위", ("EXEC.DESIGN_LINK",), True, True),
    ("E2E", "E02", "정보 부족 원문의 단계 간 보존", ("E2E.GAPS", "E2E.GAPS_RETAINED"), False, True),
    ("E2E", "E03", "실패·미실행·제외 범위의 최종 보고", ("A4.COUNTS",), True, True),
    ("E2E", "E04", "사람 승인 전 공식 자산 변경 방지", (), True, True),
]


SCENARIO_CRITERIA = {
    "A1.SCENARIO.DECISION": "A1-02", "A1.SCENARIO.NO_GAP": "A1-02",
    "A1.SCENARIO.PARTIAL": "A1-02", "A1.SCENARIO.QUESTION": "A1-02",
    "A2.SCENARIO.CANDIDATE_COUNT": "A2-01", "A2.SCENARIO.REUSE": "A2-01",
    "A2.SCENARIO.LAYERS": "A2-02", "A2.SCENARIO.INDEPENDENCE": "A2-05",
    "A3.SCENARIO.UI_VALUE": "A3-01", "A3.SCENARIO.INTERNAL_VALUE": "A3-01",
    "A3.SCENARIO.APPLY": "A3-01", "A3.SCENARIO.RESTORE": "A3-04",
    "A4.SCENARIO.CANDIDATE": "A4-02", "A4.SCENARIO.HUMAN": "A4-04",
    "A4.SCENARIO.RECOMMENDATION": "A4-04", "A4.SCENARIO.PARTIAL_REVIEW": "A4-04",
    "E2E.SCENARIO.QUESTION_HANDOFF": "E03",
}


def summarize_roles(checks):
    groups = {key: [] for key in ("COMMON", "A1", "A2", "A3", "A4", "E2E")}
    for owner, cid, objective, patterns, semantic, critical in CRITERIA:
        matched = [c for c in checks if any(fnmatchcase(c["rule"], p) for p in patterns)
                   or SCENARIO_CRITERIA.get(c["rule"]) == cid]
        states = {c["status"] for c in matched}
        complete = all(any(fnmatchcase(c["rule"], p) for c in matched) for p in patterns)
        status = ("FAIL" if "FAIL" in states else "ERROR" if "ERROR" in states
                  else "NOT_EVALUATED" if not matched else "PARTIAL" if semantic or not complete else "CHECKED")
        groups[owner].append({"criterion_id": cid, "objective": objective, "status": status,
                              "critical_risk": critical, "meaning_review_required": semantic,
                              "evidence_rules": [c["rule"] for c in matched], "score": None})
    # No automatic blame: a check failure can arise from target, dataset or evaluator.
    triage = [{"rule": c["rule"], "observation": c["reason"], "origin": "UNDETERMINED",
               "candidates": ["AGENT", "HARNESS", "DATASET", "EVALUATOR", "ENVIRONMENT"],
               "resolution": None} for c in checks if c["status"] in {"FAIL", "ERROR"}]
    return {"rubric_version": VERSION, "groups": groups, "failure_triage": triage,
            "quality_score": None, "note": "CHECKED는 해당 코드 검사 범위만 확인. PARTIAL은 의미 검토 필요. 미검사를 통과로 계산하지 않음."}


def render_roles(summary):
    labels = {"CHECKED": "자동 확인", "PARTIAL": "부분 확인 · 의미 검토 필요", "NOT_EVALUATED": "미평가", "FAIL": "위반 발견", "ERROR": "평가 오류"}
    titles = {"COMMON": "공통", "A1": "Agent 1 · 요구사항 분석", "A2": "Agent 2 · TC 설계", "A3": "Agent 3 · 계획·컴파일·실행", "A4": "Agent 4 · 규칙 기반 분석", "E2E": "전체 연결"}
    sections = []
    for key, items in summary["groups"].items():
        rows = ''.join('<tr><td>'+escape(i['criterion_id'])+'</td><td>'+escape(i['objective'])+'</td><td>'+labels[i['status']]+'</td></tr>' for i in items)
        sections.append('<details><summary>'+titles[key]+'</summary><table>'+rows+'</table></details>')
    return '<p>'+escape(summary['note'])+'</p>'+''.join(sections)
