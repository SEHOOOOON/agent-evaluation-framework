"""Independent, read-only evaluation of saved V2 runs; never invokes the target."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from importlib.metadata import version as package_version
from pathlib import Path, PureWindowsPath
import platform
import re
import subprocess
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field
from .agent_rubric import summarize_roles, render_roles
from .scenario_oracle import Expectation, evaluate_expectations

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"PASSED", "ASSERTION_FAILED", "EXECUTION_ERROR", "TIMEOUT", "SKIPPED"}
VERSION = "v2-recorded-0.4"


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(pattern=r"^V2-\d{3}$")
    run_id: str = Field(pattern=r"^RUN-\d{8}-\d{6}-[A-F0-9]{6}$")
    title: str = Field(min_length=1)
    split: Literal["development", "calibration", "holdout"] = "development"
    oracle_status: Literal["DRAFT", "HUMAN_REVIEWED"] = "DRAFT"
    expected_counts: dict[str, int]
    expected_recommendation: Literal["PASS", "HUMAN_REVIEW", "HOLD", "BLOCKED"]
    expected_gap_count: int = Field(ge=0)
    expected_finding_categories: list[str]
    require_step_links: bool = False
    human_review_topics: list[str] = Field(min_length=1)
    expectations: list[Expectation] = Field(default_factory=list)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def child(root: Path, relative: str) -> Path:
    # Windows paths must also be rejected when this evaluator runs on Linux.
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe relative artifact reference")
    if Path(relative).is_absolute() or PureWindowsPath(relative).is_absolute():
        raise ValueError("Absolute artifact reference")
    if ".." in Path(relative).parts:
        raise ValueError("Parent traversal in artifact reference")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Artifact reference escapes source")
    return result


def read(path: Path):
    if path.stat().st_size > 10_000_000:
        raise ValueError("JSON artifact exceeds read budget")
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def load_cases(path: Path) -> list[Case]:
    cases = [Case.model_validate(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not cases or len({c.case_id for c in cases}) != len(cases) or len({c.run_id for c in cases}) != len(cases):
        raise ValueError("Dataset is empty or duplicates a case/run; repeated reads are not new trials")
    for case in cases:
        if not case.expected_counts or set(case.expected_counts) - STATUSES:
            raise ValueError("Unknown execution status in oracle")
        if any(type(n) is not int or n < 0 for n in case.expected_counts.values()):
            raise ValueError("Invalid oracle count")
    return cases


def inventory(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            raise ValueError("Symlink in source run")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = digest(path)
    return files


def target_pin(target: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", "-C", str(target), *args])
    commit = git("rev-parse", "HEAD").decode().strip()
    if git("status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError("Evaluation target has tracked changes; pin a clean baseline first")
    files = git("ls-files", "-z").decode("utf-8").split("\0")
    hashes = {name: digest(child(target, name)) for name in files if name}
    return {"commit": commit, "tracked_files": hashes}


def normalized_counts(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - STATUSES:
        raise ValueError("Unknown status/count schema")
    if any(type(v) is not int or v < 0 for v in value.values()):
        raise ValueError("Counts must be nonnegative integers")
    return {key: value.get(key, 0) for key in sorted(STATUSES)}


def execution_rows(execution: dict) -> list[dict]:
    candidates = execution.get("candidate_results")
    if candidates is None:
        candidates = [execution["candidate_result"]] if execution.get("candidate_result") else []
    rows = list(candidates) + list(execution.get("regression_results", []))
    if execution.get("environment_precheck"):
        rows.append(execution["environment_precheck"])
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Execution rows absent or malformed")
    return rows


def string_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from string_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from string_values(item)


def object_list(value):
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ValueError("Expected a list of objects")
    return value


def text_list(value):
    return isinstance(value, list) and bool(value) and all(isinstance(s, str) and s.strip() for s in value)


def evaluate(run: Path, case: Case) -> dict:
    checks = []
    def check(rule, ok, explanation, critical=True):
        checks.append({"rule": rule, "status": "PASS" if ok else "FAIL", "critical": critical, "reason": explanation})
    try:
        request = read(child(run, "request.json"))
        a1 = read(child(run, "agent1_change_analysis.json"))
        a2 = read(child(run, "agent2_test_design.json"))
        execution = read(child(run, "validation_execution.json"))
        analysis = read(child(run, "agent4_analysis.json"))
        report = read(child(run, "final_report.json"))
        if any(not isinstance(value, dict) for value in (request, a1, a2, execution, analysis, report)):
            raise ValueError("Artifact must be a JSON object")
        source_text = "\n".join(string_values(request)) + "\n" + child(run, "srs_snapshot.md").read_text(encoding="utf-8")
        for manifest_name, links in (
            ("run_manifest.json", {"request_sha256":"request.json", "srs_sha256":"srs_snapshot.md", "agent1_analysis_sha256":"agent1_change_analysis.json"}),
            ("agent2_manifest.json", {"source_run_manifest_sha256":"run_manifest.json", "agent2_design_sha256":"agent2_test_design.json"}),
        ):
            manifest = read(child(run, manifest_name))
            if not isinstance(manifest, dict):
                raise ValueError("Manifest must be a JSON object")
            check("CHAIN." + manifest_name, all(manifest.get(key) == digest(child(run, name)) for key, name in links.items()), "저장 Manifest와 실제 입력·출력 파일 해시 대조. Checkpoint 판정을 정답으로 쓰지 않음")
        compact = lambda text: re.sub(r"\s+", "", text)
        conditions = object_list(a1["confirmed_conditions"])
        condition_ids = [c["condition_id"] for c in conditions]
        check("A1.CONDITION_IDS", bool(conditions) and len(condition_ids) == len(set(condition_ids)), "확정 조건 ID의 누락·중복 검사")
        check("A1.SOURCE_QUOTES", all(c.get("source_text") and compact(c["source_text"]) in compact(source_text) for c in conditions), "인용 근거가 요청 또는 당시 SRS에 실제 존재하는지 검사. 의미 일치는 별도 검토")
        test_cases = object_list(a2["test_cases"])
        ids = [tc["tc_id"] for tc in test_cases]
        check("A2.UNIQUE_TC", len(ids) == len(set(ids)), "TC ID 중복 검사. 기존 TC 재사용 적절성은 별도 검토")
        for tc in test_cases:
            prefix = "A2." + tc["tc_id"]
            ers = object_list(tc["expected_results"])
            check(prefix + ".DETAIL", text_list(tc.get("preconditions")) and text_list(tc.get("steps")) and bool(ers) and all(isinstance(er.get("statement"), str) and er["statement"].strip() for er in ers), "사전조건·조작·각 기대결과의 빈 문장 검사. 문장 상세도 점수 아님")
            references = list(tc.get("source_condition_ids", [])) + [cid for er in ers for cid in er.get("source_condition_ids", [])]
            check(prefix + ".TRACE", text_list(tc.get("source_condition_ids")) and all(text_list(er.get("source_condition_ids")) for er in ers) and all(cid in condition_ids for cid in references), "TC와 각 기대결과가 개별적으로 분석 조건에 연결됨")
            if case.require_step_links:
                check(prefix + ".TIMING", all(er.get("verify_after_step") in tc["steps"] for er in ers), "기대결과가 실제 조작 단계에 연결됨")
        rows = execution_rows(execution)
        row_ids = [r["test_id"] for r in rows]
        candidate_ids = [r["test_id"] for r in rows if r.get("source") == "NEW_AUTOMATION_CANDIDATE"]
        check("EXEC.DESIGN_LINK", all(cid in ids for cid in candidate_ids), "실행된 신규 후보마다 TC 설계가 존재함. 기존 TC만 재사용한 경우 빈 신규 설계 허용")
        check("EXEC.UNIQUE_ROWS", len(row_ids) == len(set(row_ids)), "후보 단일/목록 호환 필드는 중복 집계하지 않음")
        counts = normalized_counts(dict(Counter(row["status"] for row in rows)))
        for row in rows:
            prefix = "EXEC." + row["test_id"]
            if row["status"] == "SKIPPED":
                continue
            evidence = row.get("evidence_files", [])
            hashes = row.get("evidence_sha256", {})
            check(prefix + ".EVIDENCE", bool(evidence) and all(child(run, name).is_file() and hashes.get(name) == digest(child(run, name)) for name in evidence), "실행 증거 실제 파일과 바이트 해시 검사. evidence_complete 표시는 정답으로 사용하지 않음")
            check(prefix + ".EXIT", row.get("exit_code") == 0 if row["status"] == "PASSED" else row.get("exit_code") not in (0, None), "PASS와 실제 종료 코드의 모순 검사")
            if row.get("source") == "NEW_AUTOMATION_CANDIDATE":
                code = child(run, row["test_file"])
                check(prefix + ".CODE_HASH", code.is_file() and digest(code) == row.get("test_sha256"), "실행한 후보 코드와 해시 연결")
                try:
                    ast.parse(code.read_text(encoding="utf-8"))
                    syntax_ok = True
                except SyntaxError:
                    syntax_ok = False
                check(prefix + ".COMPILE", syntax_ok, "후보 Python 구문 검사. 실제 브라우저 재실행은 하지 않음")
        check("A4.COUNTS", counts == normalized_counts(analysis["status_counts"]) == normalized_counts(report["status_counts"]) and len(rows) == analysis["total_results"] == report["total_results"], "실행 행에서 독립 재집계한 수치를 보고서와 대조")
        check("A4.CHAIN", analysis.get("validation_execution_sha256") == digest(child(run, "validation_execution.json")) and report.get("analysis_sha256") == digest(child(run, "agent4_analysis.json")), "실행 → 분류 → 보고의 저장 근거 해시 대조")
        check("A4.ORACLE_COUNTS", counts == normalized_counts(case.expected_counts), "사전 작성한 사례별 예상 실행 분포와 대조")
        check("A4.RECOMMENDATION", report["recommendation"] == analysis["recommendation"] == case.expected_recommendation, "제품 실패/정보 부족을 올바르게 사람 검토로 보낼 수 있음")
        findings = object_list(report.get("검토_항목", []))
        analysis_findings = object_list(analysis.get("검토_항목", []))
        by_id = {r["test_id"]: r for r in rows}
        check("A4.FINDING_LINK", findings == analysis_findings and all(f.get("test_id") in by_id and f.get("status") == by_id[f["test_id"]]["status"] and f.get("evidence_files") == by_id[f["test_id"]].get("evidence_files") for f in findings), "분석·보고의 검토 항목과 실제 테스트 ID·상태·증거 연결 대조")
        check("A4.FINDINGS", sorted(f["category"] for f in findings) == sorted(case.expected_finding_categories), "사례별 예상 분류와 대조. 독립 원인 정확도 측정은 아님")
        check("E2E.GAPS", len(report.get("제외된_정보_부족", [])) == case.expected_gap_count, "정보 부족을 보고에서 누락하지 않았는지 확인")
        gaps = a1.get("information_gaps", [])
        gap_lists = [gaps, execution.get("제외된_정보_부족", []), analysis.get("제외된_정보_부족", []), report.get("제외된_정보_부족", [])]
        if any(not isinstance(items, list) or any(not isinstance(s, str) for s in items) for items in gap_lists):
            raise ValueError("Unsupported gap schema")
        check("E2E.GAPS_RETAINED", all(sorted(items) == sorted(gaps) for items in gap_lists), "정보 부족의 개수뿐 아니라 원문 내용이 실행·분석·보고까지 보존됨")
        if len({e.rule for e in case.expectations}) != len(case.expectations):
            raise ValueError("Duplicate scenario rule")
        checks.extend(evaluate_expectations(run, case.expectations))
        status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "ERROR" if any(c["status"] == "ERROR" for c in checks) else "REVIEW"
        return {"case_id": case.case_id, "run_id": case.run_id, "title": case.title, "status": status, "checks": checks,
                "oracle_status": case.oracle_status, "product_counts": counts, "pipeline_recommendation": report["recommendation"],
                "quality_score": None, "human_review_topics": case.human_review_topics,
                "note": "자동 검사 무위반도 의미 평가·사람 검토 완료를 뜻하지 않습니다."}
    except (OSError, ValueError, TypeError, KeyError, SyntaxError, AttributeError) as exc:
        # Do not expose raw parser errors, user paths, or artifact text in reports.
        checks.append({"rule": "EVALUATOR.INPUT", "status": "ERROR", "critical": False, "reason": "필수 자료·경로·계약 해석 실패: " + type(exc).__name__})
        return {"case_id": case.case_id, "run_id": case.run_id, "title": case.title,
                "status": "FAIL" if any(c["status"] == "FAIL" and c["critical"] for c in checks) else "ERROR",
                "checks": checks, "quality_score": None, "human_review_topics": case.human_review_topics}


def run_recorded(target: Path, dataset: Path, output_root: Path) -> Path:
    target, dataset, output_root = target.resolve(), dataset.resolve(), output_root.resolve()
    if output_root == target or output_root.is_relative_to(target):
        raise ValueError("Evaluation output must remain outside the target repository")
    cases = load_cases(dataset)
    pin = target_pin(target)
    run_roots = {c.run_id: child(target, "runs/" + c.run_id) for c in cases}
    before = {rid: inventory(path) for rid, path in run_roots.items()}
    if any(not value for value in before.values()):
        raise ValueError("Requested source run is missing")
    out = output_root / (datetime.now(timezone.utc).strftime("V2-EVAL-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    source_versions = {}
    for rid, path in run_roots.items():
        source_versions[rid] = {}
        manifests = ["run_manifest.json", "agent2_manifest.json"]
        manifests += [p.relative_to(path).as_posix() for p in sorted(path.glob("agent3_candidates/*/agent3_manifest.json"))]
        for name in manifests:
            try:
                payload = read(child(path, name))
                if not isinstance(payload, dict):
                    raise ValueError("Manifest must be an object")
                source_versions[rid][name] = {key: payload.get(key) for key in ("contract_version", "prompt_version", "model", "created_at")}
            except (OSError, ValueError) as exc:
                source_versions[rid][name] = {"read_error": type(exc).__name__}
        source_versions[rid]["execution_commit"] = None
    write(out / "baseline.json", {"evaluation_time_target": pin, "source_runs": before, "source_versions": source_versions,
          "dataset_sha256": digest(dataset), "evaluator_sha256": digest(Path(__file__)), "version": VERSION,
          "role_rubric_sha256": digest(Path(__file__).with_name("agent_rubric.py")),
          "scenario_oracle_sha256": digest(Path(__file__).with_name("scenario_oracle.py")),
          "runtime": {"python": platform.python_version(), "pydantic": package_version("pydantic")},
          "note": "평가 시점 코드와 과거 Run 생성 시점 코드는 다릅니다. 과거 실행 commit 미기록은 unknown으로 둡니다."})
    results = [evaluate(run_roots[c.run_id], c) for c in cases]
    for result in results:
        result["agent_evaluation"] = summarize_roles(result["checks"])
    unchanged = pin == target_pin(target) and before == {rid: inventory(path) for rid, path in run_roots.items()}
    if not unchanged:
        raise RuntimeError("Target changed during evaluation; results must not be published")
    payload = {"schema_version": "1.0", "mode": "RECORDED_REVIEW", "target_executed": False, "new_model_calls": 0,
               "stability_measured": False, "comparison_eligible": False, "quality_score": None,
               "source_unchanged": unchanged, "case_count": len(cases), "counts": dict(Counter(r["status"] for r in results)),
               "critical_failures": sum(c["status"] == "FAIL" and c["critical"] for r in results for c in r["checks"]),
               "evaluator_errors": sum(c["status"] == "ERROR" for r in results for c in r["checks"]),
               "results": results, "limitations": ["과거 3개 사례는 독립 표본이나 동일 버전 성능 비교가 아님", "사람 정답 검토 및 LLM Judge 미실행", "승인 권한·의미 충실성·Selector 적절성의 전체 검증은 미구현"]}
    write(out / "result.json", payload)
    rows = "".join(f'<tr><td>{escape(r["case_id"])}</td><td>{escape(r["title"])}</td><td>{r["status"]}</td><td>{sum(c["status"] == "FAIL" for c in r["checks"])}</td></tr>' for r in results)
    details = "".join('<h2>'+escape(r['case_id']+' · '+r['title'])+'</h2><p>사람이 확인할 내용</p><ul>'+''.join('<li>'+escape(q)+'</li>' for q in r['human_review_topics'])+'</ul><details><summary>자동 검사 근거 펼치기</summary><ul>'+''.join('<li>'+escape(c['rule']+' · '+c['status']+' · '+c['reason'])+'</li>' for c in r['checks'])+'</ul></details>' for r in results)
    html = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>V2 저장 기록 평가</title><style>body{max-width:1050px;margin:50px auto;background:#0b1018;color:#d5e1f0;font:16px/1.8 sans-serif;padding:24px}h1,h2{color:#70b7ff}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #334155}li{margin:6px 0}aside{padding:20px;background:#172333}</style><h1>QA Agent · 저장 기록 평가</h1><aside>새 AI 실행 0회 · 반복 안정성 미측정 · 품질 점수 미산정<br>REVIEW는 검사 무위반 후 의미·사람 검토가 남았다는 뜻입니다. 제품 FAIL은 곧 Agent FAIL이 아닙니다.</aside><table><tr><th>사례</th><th>내용</th><th>평가 상태</th><th>검사 위반</th></tr>'+rows+'</table>'+details+'</html>'
    role_details = ''.join('<h2>'+escape(r['case_id'])+' · 역할별 평가 범위</h2>'+render_roles(r['agent_evaluation']) for r in results)
    html = html.replace('</html>', role_details+'</html>')
    (out / "report.html").write_text(html, encoding="utf-8")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks/v2/recorded_v1.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation-runs")
    args = parser.parse_args(argv)
    try:
        out = run_recorded(args.target, args.dataset, args.output)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print("Evaluation blocked:", type(exc).__name__)
        return 4
    result = read(out / "result.json")
    print(json.dumps({"output": str(out), "counts": result["counts"], "new_model_calls": 0}, ensure_ascii=False))
    return 2 if result["counts"].get("FAIL") else 4 if result["counts"].get("ERROR") else 3


if __name__ == "__main__":
    raise SystemExit(main())
