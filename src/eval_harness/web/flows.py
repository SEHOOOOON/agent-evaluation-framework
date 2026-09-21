"""Safe summary of local full-flow records; no workspace file serving."""
import re
from ..v2_recorded import child, read
from .recorded import public_text

FLOW_ID = re.compile(r"FLOW-\d{8}-\d{6}-[a-f0-9]{6}\Z")


def flow_path(root, name):
    if not FLOW_ID.fullmatch(name):
        raise ValueError("Invalid flow ID")
    if (root / name).is_symlink() or (root / name / "result.json").is_symlink():
        raise ValueError("Linked result")
    return child(root, name + "/result.json")


def flow_list(root):
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.iterdir(), reverse=True):
        if not FLOW_ID.fullmatch(path.name) or path.is_symlink():
            continue
        try:
            if flow_path(root, path.name).is_file():
                rows.append({"id": path.name})
        except ValueError:
            continue
        if len(rows) >= 100:
            break
    return rows


def flow_summary(root, name):
    data = read(flow_path(root, name))
    if data.get("mode") not in {"TEST_DOUBLE", "FULL_FLOW_PILOT"}:
        raise ValueError("Unsupported evaluation mode")
    rows = data.get("trials")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise ValueError("Invalid trial list")
    result = {key: data.get(key) for key in (
        "mode", "status", "planned_trials", "generation_attempts", "judge_attempts",
        "api_request_attempts", "quality_assessment", "stop_reason")}
    result["id"] = name
    result["trials"] = []
    for row in rows:
        budget_stop = row.get("execution", {}).get("reason") == "GENERATION_BUDGET_EXHAUSTED"
        raw_status = row["status"]
        roles = row.get("roles", {})
        result["trials"].append({
            "trial": row["trial"], "raw_status": public_text(raw_status, 80),
            "status": "LIMIT_REACHED" if budget_stop and raw_status == "ERROR" else public_text(raw_status, 80),
            "reason": "호출 한도 도달로 재작성을 중단했습니다. 앞선 검사 결과와 초안은 원본 증거에 보존됩니다." if budget_stop else "",
            "roles": {role: "LIMIT_REACHED" if budget_stop and roles.get(role) == "ERROR"
                      else public_text(roles.get(role, "NOT_EXECUTED"), 80)
                      for role in ("A1", "A2", "A3", "A4")},
            "new_plan_not_required": row.get("new_plan_not_required", False) is True,
            "checks": [{"rule": public_text(c["rule"]), "status": public_text(c["status"]),
                        "reason": public_text(c["reason"])} for c in row.get("checks", [])[:100]],
            "judges": {role: {"status": public_text(j.get("status", "NOT_EVALUATED")),
                              "reason": public_text(j.get("reason", j.get("assessment", "")))}
                       for role, j in row.get("judges", {}).items() if role in {"A1", "A2", "A3", "A4"}},
        })
    return result
