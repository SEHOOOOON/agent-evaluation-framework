"""Read-only, bounded projection of saved V2 evaluations for the dashboard."""
from collections import Counter
import json
from pathlib import Path
import re

from ..security import redact_text

REPORT_ID = re.compile(r"V2-EVAL-[0-9]{8}-[0-9]{6}-[A-Za-z0-9]{6,16}\Z")
ROLES = ("A1", "A2", "A3", "A4", "E2E", "COMMON")
ITEM_STATES = {"CHECKED", "PARTIAL", "NOT_EVALUATED", "FAIL", "ERROR"}
CASE_STATES = {"REVIEW", "FAIL", "ERROR"}
MAX_BYTES = 4_000_000


def public_text(value, limit=2400):
    if not isinstance(value, str):
        raise ValueError("Invalid text")
    value = redact_text(value)
    value = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[비밀정보 숨김]", value)
    value = re.sub(r"[A-Za-z]:[\\/][^\s\"'<>]+", "[로컬 경로 숨김]", value)
    value = re.sub(r"/(?:Users|home|tmp|var|mnt)/[^\s\"'<>]+", "[로컬 경로 숨김]", value)
    return value[:limit]


def result_path(root: Path, report_id: str) -> Path:
    if not REPORT_ID.fullmatch(report_id):
        raise ValueError("Invalid report ID")
    root = root.resolve()
    folder = root / report_id
    target = folder / "result.json"
    if folder.is_symlink() or target.is_symlink():
        raise ValueError("Linked report not allowed")
    if folder.resolve().parent != root or target.resolve().parent != folder.resolve():
        raise ValueError("Report outside output directory")
    if not target.is_file() or target.stat().st_size > MAX_BYTES:
        raise ValueError("Missing or oversized report")
    return target


def list_reports(root: Path):
    if not root.is_dir():
        return {"reports": [], "truncated": False}
    ids = []
    for folder in root.iterdir():
        if not REPORT_ID.fullmatch(folder.name):
            continue
        try:
            result_path(root, folder.name)
        except (ValueError, OSError):
            continue
        ids.append(folder.name)
    ids.sort(reverse=True)
    return {"reports": [{"id": name} for name in ids[:100]], "truncated": len(ids) > 100}


def read_report(root: Path, report_id: str):
    target = result_path(root, report_id)
    with target.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Oversized report")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("mode") != "RECORDED_REVIEW":
        raise ValueError("Not a recorded review")
    if data.get("target_executed") is not False or data.get("stability_measured") is not False:
        raise ValueError("Unsupported live claim")
    rows = data.get("results")
    if not isinstance(rows, list) or not rows or len(rows) > 200:
        raise ValueError("Invalid cases")
    cases, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("status") not in CASE_STATES:
            raise ValueError("Invalid case")
        cid = public_text(row.get("case_id"), 120)
        if not cid or cid in seen:
            raise ValueError("Duplicate or empty case ID")
        seen.add(cid)
        checks = row.get("checks")
        if not isinstance(checks, list) or len(checks) > 2000:
            raise ValueError("Invalid checks")
        projected_checks, check_ids = [], set()
        for check in checks:
            if not isinstance(check, dict) or check.get("status") not in {"PASS", "FAIL", "ERROR", "NOT_EVALUATED"}:
                raise ValueError("Invalid check")
            rule = public_text(check.get("rule"), 200)
            if rule in check_ids:
                raise ValueError("Duplicate check")
            check_ids.add(rule)
            projected_checks.append({"rule": rule, "status": check["status"],
                                     "reason": public_text(check.get("reason", ""))})
        agent = row.get("agent_evaluation", {})
        if not isinstance(agent, dict) or not isinstance(agent.get("groups", {}), dict):
            raise ValueError("Invalid role groups")
        groups = {}
        for role in ROLES:
            items = agent.get("groups", {}).get(role, [])
            if not isinstance(items, list) or len(items) > 100:
                raise ValueError("Invalid role criteria")
            result_items, item_ids = [], set()
            for item in items:
                if not isinstance(item, dict) or item.get("status") not in ITEM_STATES:
                    raise ValueError("Invalid role state")
                item_id = public_text(item.get("criterion_id"), 100)
                refs = item.get("evidence_rules", [])
                if item_id in item_ids or not isinstance(refs, list) or len(refs) > 2000:
                    raise ValueError("Invalid role evidence")
                item_ids.add(item_id)
                refs = [public_text(ref, 200) for ref in refs]
                if any(ref not in check_ids for ref in refs):
                    raise ValueError("Unlinked role evidence")
                result_items.append({"id": item_id, "title": public_text(item.get("objective")),
                                     "status": item["status"], "evidence_rules": refs})
            counts = Counter(item["status"] for item in result_items)
            status = ("FAIL" if counts["FAIL"] else "ERROR" if counts["ERROR"]
                      else "PARTIAL" if counts["PARTIAL"] or (counts["CHECKED"] and counts["NOT_EVALUATED"])
                      else "CHECKED" if counts["CHECKED"] else "NOT_EVALUATED")
            groups[role] = {"status": status, "counts": dict(counts), "items": result_items}
        cases.append({"id": cid, "run_id": public_text(row.get("run_id", ""), 160),
                      "title": public_text(row.get("title", cid), 200), "status": row["status"],
                      "oracle_status": public_text(row.get("oracle_status", "UNKNOWN"), 80),
                      "groups": groups, "checks": projected_checks})
    counts = Counter(case["status"] for case in cases)
    if data.get("case_count") != len(cases) or data.get("counts") != dict(counts):
        raise ValueError("Inconsistent case counts")
    return {"id": report_id, "mode": "RECORDED_REVIEW", "target_executed": False,
            "stability_measured": False, "quality_score": None,
            "source_unchanged_at_review": data.get("source_unchanged") is True,
            "counts": dict(counts), "cases": cases}
