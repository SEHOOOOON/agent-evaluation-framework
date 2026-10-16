"""Version-pinned P1 execution, collection and separate role evaluation.

Preview is read-only. Execution requires an exact plan hash and an explicit
call budget. A fresh working copy is not an OS security sandbox.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
from typing import Literal
from uuid import uuid4
import zipfile

from pydantic import BaseModel, ConfigDict, Field

from .detailed_rubric import role_items, VERSION as RUBRIC_VERSION, SCORE_VERSION, PYTHON_CRITERIA
from .execution_observations import grade_run as grade_execution_observations
from .product_expectations import ExpectedMismatch, classify as classify_product_expectation
from .openai_judge import (JudgePacket, encode, preview as judge_preview, evaluate as judge_evaluate,
                           scan_payload, _unique_json_fields)
from .v2_recorded import (child, digest, read, write, target_pin, inventory, execution_rows,
                          finding_matches_execution, report_counts_match, handoff_checks)

VERSION = "full-flow-0.18"
ROLES = ("A1", "A2", "A3", "A4")


class FlowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    request: str
    oracle: str
    generation_model: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    judge_model: str = Field(default="gpt-6.1-sol", pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    trials: Literal[1, 5] = 1
    max_candidates: int = Field(default=3, ge=1, le=10)
    generation_calls_per_trial: int = Field(default=10, ge=1, le=24)
    generation_calls_total: int = Field(default=10, ge=1, le=120)
    judge_calls_total: int = Field(default=4, ge=0, le=20)
    trial_timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    product_timeout_seconds: int = Field(default=90, ge=10, le=180)
    # P1 itself does not cap generation output. A recorded A2 response used
    # 10,558 of the former 12,000 default, so new specs use the existing upper
    # bound. Historical specs that set 12000 explicitly keep that value.
    generation_max_output_tokens: int = Field(default=24000, ge=2000, le=24000)
    # Option B (2026-10-16): mismatches a person expects on the unchanged
    # product. Bound into the preview/approval hash through the spec.
    expected_product_mismatches: list[ExpectedMismatch] = Field(default=[], max_length=20)


def p2_pin():
    root = Path(__file__).resolve().parents[2]
    files = list((root / "src/eval_harness").rglob("*.py"))
    files += [root / "benchmarks/v2/TC_REFERENCE.md"]
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(files)}


def selected_files(target, request, pin):
    required = {
        "docs/01_PRODUCT_SRS.md", "docs/basic_tc_details.json",
        "product_baseline/virtual-controller.html",
        "product_baseline/pytest.ini", "product_baseline/tests/conftest.py",
        "product_baseline/tests/test_controller.py", "approved_assets/registry.json",
        request,
    }
    names = set(pin["tracked_files"])
    if not required <= names:
        raise ValueError("Required runtime inputs must be tracked in the pinned target")
    selected = {name for name in names if (
        name.startswith("src/qa_pipeline") and name.endswith(".py")
        or name.startswith("approved_assets/") and name.endswith((".json", ".py", ".md"))
        or name in required
    )}
    if "src/qa_pipeline_orchestrator.py" not in selected:
        raise ValueError("Unsupported target entrypoint")
    for name in selected:
        path = child(target, name)
        if path.is_symlink() or path.stat().st_size > 10_000_000:
            raise ValueError("Linked or oversized runtime input")
    return {name: pin["tracked_files"][name] for name in sorted(selected)}


def make_preview(target: Path, spec: FlowSpec, config_root: Path):
    target = target.resolve()
    spec = FlowSpec.model_validate(spec.model_dump())
    pin = target_pin(target)
    selected = selected_files(target, spec.request, pin)
    if not runtime_unchanged(target, selected):
        raise ValueError("Approved asset inventory differs from the pinned files")
    request = read(child(target, spec.request))
    oracle = read(child(config_root.resolve(), spec.oracle))
    if not isinstance(oracle, dict) or oracle.get("case_id") != spec.case_id:
        raise ValueError("Independent oracle must match the case")
    if oracle.get("status") not in {"DRAFT", "HUMAN_REVIEWED"}:
        raise ValueError("Oracle review status is required")
    if not isinstance(oracle.get("expectations"), list) or not oracle["expectations"]:
        raise ValueError("Independent expected conditions must be supplied")
    scan_payload(request)
    scan_payload(oracle)
    # Validate the SRS text before allowing even a generation request.
    srs = child(target, "docs/01_PRODUCT_SRS.md").read_text(encoding="utf-8")
    scan_payload(srs)
    plan = {
        "version": VERSION, "spec": spec.model_dump(), "target_commit": pin["commit"],
        "runtime_hashes": selected, "evaluator_hashes": p2_pin(),
        "oracle": oracle, "request": request, "srs_sha256": selected["docs/01_PRODUCT_SRS.md"],
        "rubric_version": RUBRIC_VERSION,
        "scoring_profile": SCORE_VERSION,
        "python_criteria": sorted(PYTHON_CRITERIA),
        "generation_destination": "https://api.openai.com/v1/responses",
        "judge_destination": "https://api.openai.com/v1/responses",
        "generation_sdk_retries": 0, "judge_sdk_retries": 0,
        "checkpoint_rewrites": "P1 existing bounded policy; every parse attempt is charged",
        "p1_call_budget_includes": ["generation", "checkpoint rewrites", "grounding reviews"],
        "external_publish": False, "official_registration": False,
        "maximum_api_attempts": spec.generation_calls_total + spec.judge_calls_total,
        "scope": "A1-A4 + checkpoint artifacts + partial E2E code checks",
        "transmission": {
            "generation": ["request", "related SRS", "existing TC catalog", "actual upstream outputs",
                           "limited UI observation", "checkpoint repair feedback", "grounding review inputs"],
            "judge": ["request/SRS", "independent oracle", "user TC writing criteria",
                      "role outputs", "request/repair traces", "plan", "neutral execution/report"],
            "excluded": ["credentials", "absolute local paths", "full HTML", "screenshots", "trace archives", "automation source code"],
        },
        "limitations": [
            "Downstream exact payloads depend on this run; each is scanned before sending.",
            "Fresh copy and hashes are not OS-level security isolation.",
            "DRAFT oracle or uncalibrated judge cannot establish final quality PASS.",
            "One trial is a rehearsal; five trials are exploratory, not statistical assurance.",
        ],
    }
    return {"plan": plan, "approval_sha256": sha256(encode(plan).encode()).hexdigest()}


def clean_environment(*, include_key=False):
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE",
               "LOCALAPPDATA", "APPDATA", "COMSPEC", "PATHEXT", "PLAYWRIGHT_BROWSERS_PATH"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    # Existing Windows .pth files may use the locale encoding. UTF-8 mode would
    # break interpreter startup before our code runs; streams can be UTF-8 alone.
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    if include_key:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        env["OPENAI_API_KEY"] = key
    return env


def run_worker(work: Path, config: dict):
    config_path = work / "worker_config.json"
    write(config_path, config)
    with (work / "worker.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "eval_harness.flow_worker", str(config_path)],
            cwd=work, env=clean_environment(include_key=True), stdout=log, stderr=log,
            start_new_session=os.name != "nt",
        )
        try:
            proc.wait(timeout=config["trial_timeout_seconds"])
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, check=False)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return {"status": "ERROR", "reason": "WORKER_TIMEOUT", "stages": {},
                    "server_completion": "UNKNOWN"}
    output = work / "worker_result.json"
    if not output.is_file():
        return {"status": "ERROR", "reason": "WORKER_RESULT_MISSING", "stages": {}}
    result = read(output)
    if proc.returncode != 0:
        result.update(status="ERROR", reason="WORKER_PROCESS_ERROR")
    return result


def waiting_user_notice(run: Path, worker: dict) -> dict | None:
    """Display a hash-linked P1 pause; never override execution or grading."""
    if worker.get("status") != "BLOCKED" or worker.get("stages") != {"agent1": 2}:
        return None
    try:
        names = ("orchestrator_manifest.json", "run_manifest.json",
                 "checkpoint1.json", "agent1_change_analysis.json")
        paths = [safe_evidence_path(run, name) for name in names]
        if any(p.stat().st_size > 2_000_000 for p in paths):
            return None
        orchestrator, manifest, checkpoint, analysis = [read(p) for p in paths]
        if not all(isinstance(value, dict) for value in (orchestrator, manifest, checkpoint, analysis)):
            return None
        if not (
            orchestrator.get("run_id") == manifest.get("run_id") == run.name
            and orchestrator.get("status") == "STOPPED"
            and orchestrator.get("stopped_at") == "agent1"
            and orchestrator.get("stage_exit_codes") == {"agent1": 2}
            and manifest.get("stage") == "AGENT_1_CP1"
            and manifest.get("status") == checkpoint.get("status") == "PASS"
            and manifest.get("handoff_status") == checkpoint.get("handoff_status") == "PAUSE"
            and analysis.get("decision") == "WAITING_FOR_USER"
            and orchestrator.get("agent1_manifest_sha256") == digest(paths[1])
            and manifest.get("checkpoint1_sha256") == digest(paths[2])
            and manifest.get("agent1_analysis_sha256") == digest(paths[3])
        ):
            return None
        questions = analysis.get("user_questions")
        if not isinstance(questions, list) or not questions or any(
            not isinstance(q, str) or not q.strip() for q in questions
        ):
            return None
        return {"status": "WAITING_FOR_USER", "questions": questions}
    except (ValueError, OSError, TypeError):
        return None


def stage_states(run: Path, worker: dict):
    stages = worker.get("stages", {})
    result = {}
    for role, key, artifact in (
        ("A1", "agent1", "agent1_change_analysis.json"),
        ("A2", "agent2", "agent2_test_design.json"),
        ("A3", "agent3", "agent3_run_summary.json"),
        ("A4", "agent4", "final_report.json"),
    ):
        if key not in stages:
            result[role] = "NOT_EXECUTED"
        elif not (run / artifact).is_file():
            result[role] = "ERROR"
        else:
            result[role] = "COMPLETED" if stages[key] == 0 else "BLOCKED" if stages[key] == 2 else "ERROR"
    # New-plan N/A does not mean the A3 execution role was skipped.
    selection = read(run / "agent3_selection.json") if (run / "agent3_selection.json").is_file() else {}
    summary = read(run / "agent3_run_summary.json") if (run / "agent3_run_summary.json").is_file() else {}
    if selection.get("status") == "NOT_REQUIRED" and "execute" in stages:
        result["A3"] = "COMPLETED" if stages["execute"] == 0 else "BLOCKED"
    elif "execute" in stages and (selection.get("status") == "EXCLUDED" or summary.get("status") == "PARTIAL"):
        result["A3"] = "PARTIAL" if stages["execute"] == 0 else "BLOCKED"
    return result, selection.get("status") == "NOT_REQUIRED"


def local_checks(run: Path, source_unchanged: bool, copy_unchanged: bool):
    checks = []
    def add(rule, status, reason):
        checks.append(dict(rule=rule, status=status, reason=reason))
    add("E04.ASSET_PROTECTION", "PASS" if source_unchanged and copy_unchanged else "FAIL",
        "원본 기준 자산과 회차 사본의 코드·SRS·공식 TC·제품 해시 대조")
    files = {}
    for name in ("request.json", "agent1_change_analysis.json", "agent2_test_design.json",
                 "validation_execution.json", "agent4_analysis.json", "final_report.json"):
        if (run / name).is_file():
            files[name] = read(run / name)
    report = files.get("final_report.json")
    execution = files.get("validation_execution.json")
    if not report or not execution:
        add("E01.COMPLETION", "NOT_EVALUATED", "최종 실행·보고 자료 미도달; 완료로 판단하지 않음")
        return checks
    rows = execution_rows(execution)
    analysis = files.get("agent4_analysis.json", {})
    count_status = "PASS"
    for output in (analysis, report):
        try:
            if not report_counts_match(rows, output):
                count_status = "FAIL"
        except (KeyError, ValueError, TypeError, AttributeError):
            if count_status != "FAIL":
                count_status = "NOT_EVALUATED"
    add("A4.COUNTS", count_status, "실행 행의 상태·전체·제품·환경·고정 사례 수량을 분석·보고와 독립 대조")
    add("C01.REPORT_CHAIN", "PASS" if (
        analysis.get("validation_execution_sha256") == digest(run / "validation_execution.json")
        and report.get("analysis_sha256") == digest(run / "agent4_analysis.json")
    ) else "FAIL", "실제 실행→분석→최종 보고의 파일 해시 연결")
    checks.extend(handoff_checks(files))
    add("E01.SEMANTIC_COVERAGE", "NOT_EVALUATED",
        "요청 전체의 의미상 검증 충분성과 Checkpoint 과잉 차단은 독립 사람 정답 대조 필요")
    add("A4.CAUSE_ACCURACY", "NOT_EVALUATED",
        "원인 분류의 정답은 결과 라벨에서 복사하지 않음; 중립 증거와 사람 판정 필요")
    return checks


def runtime_unchanged(root, hashes):
    if not all(child(root, n).is_file() and digest(child(root, n)) == h for n, h in hashes.items()):
        return False
    expected_assets = {n.removeprefix("approved_assets/"): h for n, h in hashes.items()
                       if n.startswith("approved_assets/")}
    return inventory(root / "approved_assets") == expected_assets


def archive_evidence(work, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(work.rglob("*")):
            if "__pycache__" in path.parts:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(work.resolve()):
                raise ValueError("External link in trial evidence")
            if path.is_file():
                archive.write(path, path.relative_to(work).as_posix())
    return digest(destination)


def comparison_snapshot(run):
    """Descriptive output fingerprints, never a semantic correctness grader."""
    def stable(value):
        volatile = {"tc_id", "condition_id", "request_id", "created_at",
                    "source_condition_ids", "source_step_ids", "assertion_id", "action_id"}
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in volatile}
        if isinstance(value, list):
            return [stable(v) for v in value]
        return value
    values = {}
    a1 = run / "agent1_change_analysis.json"
    if a1.is_file():
        data = read(a1)
        values["A1"] = stable({k: data.get(k) for k in (
            "decision", "before_condition", "after_condition", "confirmed_conditions",
            "information_gaps", "user_questions")})
    a2 = run / "agent2_test_design.json"
    if a2.is_file():
        data = read(a2)
        values["A2"] = {"test_cases": stable(data.get("test_cases", [])),
                        "selected_existing_ids": sorted(x["tc_id"] for x in data.get("관련_기존_TC", []))}
    plans = [safe_evidence_path(run, p.relative_to(run).as_posix())
             for p in sorted(run.rglob("agent3_automation_plan.json"))]
    if plans:
        values["A3"] = [stable(read(p)) for p in plans]
    report = run / "final_report.json"
    if report.is_file():
        data = read(report)
        values["A4"] = {k: data.get(k) for k in (
            "status_counts", "recommendation", "제외된_정보_부족", "최종_확인_사항", "검토_항목")}
    return {role: {"fingerprint": sha256(encode(value).encode()).hexdigest(), "values": value}
            for role, value in values.items()}


def judge_projection(value, workspace, omissions, pointer=""):
    """Explicit metadata-path redaction only; raw local artifacts remain intact."""
    if isinstance(value, dict):
        return {k: judge_projection(v, workspace, omissions, pointer + "/" + k) for k, v in value.items()}
    if isinstance(value, list):
        return [judge_projection(v, workspace, omissions, pointer + "/" + str(i)) for i, v in enumerate(value)]
    if isinstance(value, str):
        changed = value
        for root in (str(workspace).replace("\\", "\\\\"), str(workspace), workspace.as_posix()):
            changed = changed.replace(root, "[WORKSPACE]")
        if changed != value:
            omissions.append(pointer)
        return changed
    return value


def catalog_evidence(snapshot):
    """Keep TC content; withhold approval metadata rather than relax secret scans.

    Maintenance authorization is a local approval record, not a TC criterion.
    Never whitelist an authentication-shaped key or send its value under an alias.
    The source catalog is hash-verified by the caller and remains unchanged.
    """
    projected = json.loads(encode(snapshot))
    omissions = []
    if not isinstance(projected, dict):
        return projected, omissions
    for index, entry in enumerate(projected.get("approved_assets", [])):
        if not isinstance(entry, dict) or not isinstance(entry.get("test_case_json"), str):
            continue
        try:
            document = json.loads(entry["test_case_json"], object_pairs_hook=_unique_json_fields)
        except ValueError:
            continue  # The unchanged content still goes through the safety scanner.
        maintenance = document.get("maintenance_revision") if isinstance(document, dict) else None
        if not isinstance(maintenance, dict) or "authorization" not in maintenance:
            continue
        original = entry["test_case_json"]
        withheld = maintenance.pop("authorization")
        omissions.append({"path": f"approved_assets/{index}/test_case_json/maintenance_revision/authorization",
                          "reason": "LOCAL_APPROVAL_METADATA_NOT_JUDGE_INPUT",
                          "source_test_case_json_sha256": sha256(original.encode("utf-8")).hexdigest(),
                          "withheld_value_sha256": sha256(encode(withheld).encode("utf-8")).hexdigest()})
        entry["test_case_json"] = encode(document)
    return projected, omissions


def compact_evidence(source):
    """Keep comparison inputs/output direct; deduplicate repeated trace evidence.

    Context and artifact are the material being compared, not an alternate
    implementation of it. Never hide their values behind transport references.
    Other source roots may reference the same exact earlier value. No content
    is rewritten, removed, or converted into a verdict by this projection.
    """
    seen = {}
    direct_text = {}

    def factor_text(value):
        # Mixed prose/JSON requests can embed an exact core value. Keep literal
        # separators and order; never normalize, summarize or infer equivalence.
        parts = [value]
        for text, target in sorted(direct_text.items(), key=lambda item: -len(item[0])):
            next_parts = []
            for part in parts:
                if not isinstance(part, str) or text not in part:
                    next_parts.append(part)
                    continue
                fragments = part.split(text)
                for index, fragment in enumerate(fragments):
                    if index:
                        next_parts.append({"evidence_reference": target})
                    if fragment:
                        next_parts.append(fragment)
            parts = next_parts
        if all(isinstance(part, str) for part in parts):
            return value
        projected = {"evidence_text_parts": parts,
                     "raw_utf8_sha256": sha256(value.encode("utf-8")).hexdigest()}
        if len(encode(projected).encode("utf-8")) >= len(encode(value).encode("utf-8")):
            return value
        # Otherwise a secret/path split across fragments could evade the final scan.
        scan_payload(value)
        return projected

    def visit(value, path):
        encoded = encode(value)
        direct = bool(path and path[0] in {"context", "artifact"})
        if len(encoded.encode("utf-8")) >= 512:
            if encoded in seen and not direct:
                return {"evidence_reference": seen[encoded]}
            if isinstance(value, str):
                if direct:
                    direct_text.setdefault(value, path)
                else:
                    projected = factor_text(value)
                    if not isinstance(projected, str):
                        return projected
            seen.setdefault(encoded, path)
        if isinstance(value, dict):
            return {k: visit(v, path + [k]) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v, path + [str(i)]) for i, v in enumerate(value)]
        return value
    return visit(source, [])


def request_evidence(call, raw_sha256=None):
    """Expose JSON user inputs as values; raw request bytes stay in the ZIP."""
    result = json.loads(encode(call))
    if result.get("purpose") == "grounding":
        if raw_sha256 is None:
            raise ValueError("Grounding request omission requires its original file hash")
        result.pop("payload", None)
        result.update(payload_omitted="GROUNDING_BODY_NOT_ROLE_GENERATION",
                      raw_request_sha256=raw_sha256)
        return result
    for message in result.get("payload", {}).get("input", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.lstrip().startswith(("{", "[")):
            try:
                message["content"] = {"parsed_json": json.loads(content),
                                      "raw_utf8_sha256": sha256(content.encode()).hexdigest()}
            except ValueError:
                pass
    return result


def python_scores(run, checks):
    """Read-only fact grades, independent of the target's PASS declarations.

    E04 remains unscored without a real approval lifecycle. An observed asset
    violation is still a failure. Missing evidence is never a fabricated pass.
    """
    grades = {cid: dict(criterion_id=cid, score=None, verdict="REVIEW",
                       reason="독립 확인에 필요한 실행·승인 근거 부족", evidence=[])
              for cid in sorted(PYTHON_CRITERIA)}
    grades["A3-03"] = grade_execution_observations(run)

    def assign(cid, ok, reason):
        grades[cid].update(score=5 if ok else 0, verdict="PASS" if ok else "FAIL", reason=reason)

    if any(c["rule"] == "E04.ASSET_PROTECTION" and c["status"] == "FAIL" for c in checks):
        assign("E04", False, "승인 없는 평가 실행 중 고정 자산 변경이 확인됨")
    try:
        execution = read(child(run, "validation_execution.json"))
        report = read(child(run, "final_report.json"))
        analysis = read(child(run, "agent4_analysis.json"))
        rows = execution_rows(execution)
        ids = [r.get("test_id", r.get("tc_id")) for r in rows]
        unique = all(isinstance(i, str) and i for i in ids) and len(ids) == len(set(ids))
        count_ok = True
        for output in (analysis, report):
            try:
                if not report_counts_match(rows, output):
                    count_ok = False
            except (KeyError, ValueError, TypeError, AttributeError):
                if count_ok is not False:
                    count_ok = None
        if count_ok is not None:
            assign("A4-01", count_ok, "원본 실행 행의 중복·상태·제품·환경·고정 사례 합계를 독립 대조")
        grades["A4-01"]["evidence"] = [dict(file="validation_execution.json", sha256=digest(run / "validation_execution.json")),
                                            dict(file="final_report.json", sha256=digest(run / "final_report.json"))]
        # Absent fields are unobserved, not equivalent to an empty findings list.
        findings = report["검토_항목"]
        analysis_findings = analysis["검토_항목"]
        if not isinstance(findings, list) or not isinstance(analysis_findings, list):
            raise ValueError("Invalid findings")
        same_run = execution["run_id"] == analysis["run_id"] == report["run_id"] == run.name
        linked = (same_run and unique and findings == analysis_findings
                  and all(finding_matches_execution(f, rows, execution.get("status")) for f in findings)
                  and analysis.get("validation_execution_sha256") == digest(run / "validation_execution.json")
                  and report.get("analysis_sha256") == digest(run / "agent4_analysis.json"))
        if not linked:
            assign("A4-03", False, "Run·TC·상태 또는 실행→보고 연결 불일치")
        for row in rows:
            files, hashes = row.get("evidence_files"), row.get("evidence_sha256")
            if files is None or hashes is None:
                raise ValueError("Evidence manifest missing")
            if not isinstance(files, list) or not isinstance(hashes, dict):
                raise ValueError("Evidence manifest malformed")
            if row["status"] != "SKIPPED" and not files:
                raise ValueError("Execution evidence absent")
            for name in files:
                path = safe_evidence_path(run, name)
                linked = linked and path.is_file() and digest(path) == hashes.get(name)
                if not linked:
                    assign("A4-03", False, "실행→보고 연결 또는 참조 증거 파일 해시 불일치")
        assign("A4-03", linked, "Run·TC·상태·분류 근거 연결과 참조 파일 해시를 대조; 화면 내용의 의미 판정은 아님")
        grades["A4-03"]["evidence"] = [dict(file="agent4_analysis.json", sha256=digest(run / "agent4_analysis.json"))]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Retain facts already established; unavailable portions stay REVIEW.
        pass
    return dict(status="COMPLETED", mode="PYTHON", scoring_profile=SCORE_VERSION, rubric_version=RUBRIC_VERSION,
                judgments=list(grades.values()), approval_revalidation=approval_revalidation_status(run))


def approval_revalidation_status(run):
    """Local collection-time status, never a rescore or permission to register.

    Old assessment scores describe the original execution. A later retry can
    block registration without making those original grades wrong.
    """
    run = run.resolve()
    result = dict(scope="COLLECTION_SNAPSHOT_NOT_LIVE", status="NO_RETRY_RECORD",
                  registration="P1_CHECK_REQUIRED", scores_changed=False, items=[])
    root = run / "asset_revalidation"
    if root.is_symlink():
        result.update(status="REVIEW_REQUIRED", registration="UNCONFIRMED")
        return result
    if not root.exists():
        return result
    if not root.is_dir():
        result.update(status="REVIEW_REQUIRED", registration="UNCONFIRMED")
        return result
    for source in sorted(root.glob("*/latest.json")):
        item = dict(tc_id=source.parent.name, status="REVIEW_REQUIRED", outcome=None,
                    registration="UNCONFIRMED", reason="재시험 자료의 연결을 확인할 수 없습니다.")
        result["items"].append(item)
        try:
            name = source.relative_to(run).as_posix()
            path = safe_evidence_path(run, name)
            record = read(path)
            stamp = digest(path)
            if record.get("run_id") != run.name or record.get("tc_id") != source.parent.name:
                raise ValueError("Retry identity mismatch")
            wrapper = read(safe_evidence_path(run, "validation_manifest.json"))
            if wrapper.get("run_id") != run.name or wrapper.get("stage") != "VALIDATION_EXECUTION":
                raise ValueError("Validation identity mismatch")
            execution_path = safe_evidence_path(run, "validation_execution.json")
            if digest(execution_path) != wrapper.get("validation_execution_sha256"):
                raise ValueError("Validation hash mismatch")
            execution = read(execution_path)
            if execution.get("run_id") != run.name:
                raise ValueError("Execution Run mismatch")
            selected = [r for r in execution_rows(execution)
                        if r.get("test_id") == record["tc_id"] and r.get("source") == "NEW_AUTOMATION_CANDIDATE"]
            links = [r for r in wrapper.get("source_agent3_artifacts", []) if r.get("tc_id") == record["tc_id"]]
            if len(selected) != 1 or len(links) != 1:
                raise ValueError("Ambiguous retry TC")
            manifest_path = safe_evidence_path(run, links[0]["agent3_manifest_file"])
            if manifest_path.name != "agent3_manifest.json" or digest(manifest_path) != links[0].get("agent3_manifest_sha256"):
                raise ValueError("Candidate manifest mismatch")
            manifest = read(manifest_path)
            if manifest.get("run_id") != run.name or manifest.get("tc_id") != record["tc_id"]:
                raise ValueError("Candidate identity mismatch")
            code = safe_evidence_path(manifest_path.parent / "candidates", manifest["candidate_file"])
            code_hash = digest(code)
            if not code_hash == manifest.get("candidate_sha256") == record.get("candidate_sha256") == selected[0].get("test_sha256"):
                raise ValueError("Retry code mismatch")
            # Today's product is checked only if it is available in this Run's
            # workspace. Otherwise match the originally verified execution.
            target = safe_evidence_path(run.parent.parent, "product_baseline/virtual-controller.html")
            target_hash = digest(target) if target.is_file() else selected[0].get("target_sha256")
            if not isinstance(target_hash, str) or len(target_hash) != 64 or record.get("target_sha256") != target_hash:
                raise ValueError("Retry product missing or changed")
            outcome = record.get("outcome")
            allowed = {"PASS", "PRODUCT_MISMATCH_CANDIDATE", "AUTOMATION_ERROR", "ENVIRONMENT_ERROR", "TIMEOUT", "INCOMPLETE"}
            if outcome not in allowed:
                raise ValueError("Unknown retry outcome")
            if outcome == "PASS":
                files, hashes = record.get("evidence_files"), record.get("evidence_sha256")
                if record.get("evidence_complete") is not True or not isinstance(files, list) or not files or not isinstance(hashes, dict):
                    raise ValueError("Retry evidence missing")
                evidence_root = safe_evidence_path(run, "asset_revalidation/" + record["tc_id"] + "/" + record["attempt_id"] + "/evidence")
                for file in files:
                    evidence = safe_evidence_path(run, file)
                    if not evidence.is_relative_to(evidence_root) or digest(evidence) != hashes.get(file):
                        raise ValueError("Retry evidence mismatch")
            if digest(path) != stamp:
                raise ValueError("Retry changed while collecting")
            item.update(status="VERIFIED", outcome=outcome, source=name, source_sha256=stamp,
                        product_scope="CURRENT_WORKSPACE" if target.is_file() else "ORIGINAL_EXECUTION_ONLY",
                        registration="P1_CHECK_REQUIRED" if outcome == "PASS" else "BLOCKED_BY_RETRY")
            item["reason"] = "재시험은 통과했지만 공식 등록은 P1의 전체 승인 검사가 필요합니다." if outcome == "PASS" else "재시험이 실패·미완료 상태입니다. 과거 점수와 별개로 등록 전 확인이 필요합니다."
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Untrusted bodies/paths never become display text or a false PASS.
            pass
    if result["items"]:
        result["status"] = "REVIEW_REQUIRED" if any(i["status"] != "VERIFIED" for i in result["items"]) else "COLLECTED"
        result["registration"] = "BLOCKED_BY_RETRY" if any(i["registration"] == "BLOCKED_BY_RETRY" for i in result["items"]) else "UNCONFIRMED" if result["status"] == "REVIEW_REQUIRED" else "P1_CHECK_REQUIRED"
    return result


def safe_evidence_path(root, name):
    path = child(root, name)
    original = root / name
    if any(p.is_symlink() for p in [original, *original.parents] if p.is_relative_to(root)):
        raise ValueError("Linked evidence")
    return path


def execution_support(run, work, execution, *, code_references=(), trial_documents=(), include_code=True):
    """Only referenced, hash-verified logs/code; no HTML/images or silent cuts."""
    result = {"files": {}, "omissions": []}
    remaining = 40_000
    references = {}
    try:
        rows = execution_rows(execution)
    except (ValueError, TypeError, AttributeError):
        rows = []
        if not trial_documents:
            result["omissions"].append("실행 행 없음/해석 불가")

    def include(path, label, expected):
        # Path equality handles Windows case aliases too. Never use a display
        # label as file identity, and do not allocate bytes until all references
        # to the file have been checked. Original labels stay in source records.
        references.setdefault(path.resolve(), []).append((label, expected))

    def log_reference(base, name, expected):
        path = safe_evidence_path(base, name)
        label = path.relative_to(run.resolve()).as_posix()
        if path.suffix.lower() != ".txt":
            result["omissions"].append(dict(file=label, reason="UNSUPPORTED_LOG_TYPE"))
            return
        include(path, label, expected)

    code_roots = [work / "p1/approved_assets", work / "p1/product_baseline/tests"]
    code_files = [p for root in code_roots for p in root.rglob("*.py")
                  if not p.is_symlink() and p.stat().st_size <= 200_000]
    for row in rows:
        # Only the stdout/stderr actually named by this execution row.
        hashes = row.get("evidence_sha256", {})
        for name in row.get("evidence_files", []):
            if Path(name).name != "native-restoration.json":
                continue
            path = safe_evidence_path(run, name)
            try:
                if path.stat().st_size > 200_000 or not hashes.get(name) or digest(path) != hashes[name]:
                    raise ValueError("Native restoration evidence mismatch")
                record = read(path)
                if record.get("contract_version") != "native-recovery-1.0" or record.get("test_id") != row.get("test_id"):
                    raise ValueError("Native restoration identity mismatch")
                original, restored = record.get("original", {}), record.get("restored", {})
                required = {'devices','selected','pending','gateway','multi','cards','panel'}
                if (not isinstance(original, dict) or not isinstance(restored, dict) or set(original) != required
                        or not isinstance(original['devices'], list) or not isinstance(original['cards'], list)
                        or not original['devices'] or len(original['cards']) != len(original['devices'])):
                    raise ValueError("Incomplete baseline")
                comparisons = {key: (key in restored and json.dumps(original[key], sort_keys=True) == json.dumps(restored[key], sort_keys=True))
                               for key in sorted(required)}
                result.setdefault("native_recovery", []).append(dict(file=name, sha256=hashes[name],
                    test_id=row['test_id'], status_as_recorded=record.get('status'), method=record.get('method'),
                    device_count=len(original['devices']), comparisons=comparisons,
                    verification='MATCH' if set(restored) == required and all(comparisons.values()) and record.get('status') in ('RESTORED','UNCHANGED') else 'NOT_CONFIRMED',
                    scope='Hash-verified snapshot comparison; product assertions and model quality are separate. Full snapshots retained locally.'))
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                result['omissions'].append(dict(file=name, reason='NATIVE_RECOVERY_UNVERIFIED'))
        for key in ("stdout_file", "stderr_file"):
            name = row.get(key)
            if not name:
                continue
            log_reference(run, name, hashes.get(name))
        name = row.get("test_file")
        if not name:
            continue
        path = safe_evidence_path(run, name)
        if path.suffix.lower() != ".py":
            result["omissions"].append(dict(file=name, reason="UNSUPPORTED_CODE_TYPE"))
            continue
        if path.is_file():
            include(path, path.relative_to(run.resolve()).as_posix(), row.get("test_sha256"))
        else:
            matches = [p for p in code_files if digest(safe_evidence_path(work / "p1", p.relative_to(work / "p1").as_posix()))
                       == row.get("test_sha256")]
            if matches:
                p = matches[0]  # Identical bytes; no TC-title or scenario-specific matching.
                include(p, "pinned/" + p.relative_to(work / "p1").as_posix(), row["test_sha256"])
            else:
                result["omissions"].append(dict(file=name, reason="PINNED_CODE_HASH_NOT_FOUND"))
    # A trial can exist without the final validation bundle. Its own hashes
    # establish log identity, not final acceptance or successful execution.
    for key, trial, manifest in trial_documents:
        trial_path = safe_evidence_path(run, key)
        folder = trial_path.parent
        tc_id = manifest.get("tc_id") or (folder.name if folder != run.resolve() else None)
        if not tc_id:
            result["omissions"].append(dict(file=key, reason="TRIAL_TARGET_UNAVAILABLE"))
            continue
        evidence_dir = safe_evidence_path(folder / "evidence", tc_id)
        hashes = trial.get("evidence_sha256", {})
        linked = True
        if manifest:
            name = manifest.get("trial_file")
            linked = bool(name and safe_evidence_path(folder, name) == trial_path
                          and manifest.get("trial_sha256") == digest(trial_path))
            if not linked:
                result["omissions"].append(dict(file=key, reason="TRIAL_LINK_UNVERIFIED"))
        for field in ("stdout_file", "stderr_file"):
            name = trial.get(field)
            if not name:
                continue
            log_reference(evidence_dir, name, hashes.get(name) if linked else None)
            if manifest:
                log_reference(evidence_dir, name, manifest.get("trial_evidence_sha256", {}).get(name)
                              if linked else None)

    # Final generation manifests can supply code before validation execution.
    # They establish file identity, not proof that the code ran or passed.
    for name, expected in code_references:
        path = safe_evidence_path(run, name)
        if path.suffix.lower() != ".py":
            result["omissions"].append(dict(file=name, reason="UNSUPPORTED_CODE_TYPE"))
            continue
        include(path, path.relative_to(run.resolve()).as_posix(), expected)

    # Fixed ordering makes selection independent of execution-row ordering.
    groups = sorted(references.items(), key=lambda item: min(label for label, _ in item[1]))
    verified = []
    for path, refs in groups:
        label = min(label for label, _ in refs)
        try:
            with path.open("rb") as handle:
                data = handle.read(24_001)
            observed = digest(path) if len(data) > 24_000 else sha256(data).hexdigest()
            if any(not expected or observed != expected for _, expected in refs):
                raise ValueError("Hash mismatch or absent")
            if path.suffix.lower() == ".py" and not include_code:
                result["files"][label] = dict(sha256=observed, bytes=path.stat().st_size,
                                               delivery="LOCAL_ONLY_CODE")
                continue
            verified.append((path, label, observed, data))
        except (OSError, ValueError, UnicodeError):
            result["omissions"].append(dict(file=label, reason="MISSING_OR_UNVERIFIED"))
    # Several failing logs share one budget. Without shares the first two large
    # logs took all 40,000 bytes and later failures were omitted entirely
    # (2026-10-15 check). Small logs stay whole; large ones get equal tails of
    # at least 4,000 bytes; a log that cannot get that much is still omitted.
    shares, left = {}, remaining
    logs = sorted(((min(len(d), 24_000), label) for _, label, _, d in verified
                   if Path(label).suffix.lower() == ".txt"))
    for index, (demand, label) in enumerate(logs):
        share = left // (len(logs) - index)
        shares[label] = demand if demand <= share else share if share >= 4_000 else 0
        left -= shares[label]
    for path, label, observed, data in verified:
        try:
            size = len(data)
            budget = min(24_000, remaining, shares.get(label, 24_000))
            if size > budget and path.suffix.lower() == ".txt" and budget >= 4_000:
                # A pytest failure, its observation records and restoration
                # lines are printed last. Keep that end from a whole line
                # instead of dropping the log; the hash covers the full file.
                total = path.stat().st_size
                with path.open("rb") as handle:
                    handle.seek(max(0, total - budget))
                    tail = handle.read(budget)
                start = tail.find(b"\n") + 1
                if not start or start == len(tail):
                    # No whole line fits; do not send a fragment.
                    result["omissions"].append(dict(file=label, reason="SIZE_LIMIT", sha256=observed))
                    continue
                tail = tail[start:]
                text = tail.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                result["files"][label] = dict(sha256=observed, content=text, delivery="TAIL_ONLY",
                                               bytes_total=total, bytes_included=len(tail))
                remaining -= len(tail)
                continue
            if size > budget:
                result["omissions"].append(dict(file=label, reason="SIZE_LIMIT", sha256=observed))
                continue
            text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            result["files"][label] = dict(sha256=observed, content=text)
            remaining -= size
        except (OSError, ValueError, UnicodeError):
            result["omissions"].append(dict(file=label, reason="MISSING_OR_UNVERIFIED"))
    return result


def human_review_support(run):
    """The generated review document is guidance, not a human approval."""
    name = "사람_최종_검토_manifest.json"
    path = safe_evidence_path(run, name)
    if not path.is_file():
        return {"status": "UNAVAILABLE", "reason": "NO_REVIEW_DOCUMENT_MANIFEST"}
    manifest = read(path)
    if manifest.get("run_id") != run.name or manifest.get("stage") != "HUMAN_FINAL_REVIEW_DOCUMENT":
        raise ValueError("Human review document identity mismatch")
    if manifest.get("document_file") != "사람_최종_검토.md":
        raise ValueError("Unsupported human review document")
    for filename, field in (("final_report.json", "final_report_sha256"),
                            ("validation_execution.json", "validation_execution_sha256"),
                            (manifest["document_file"], "document_sha256")):
        source = safe_evidence_path(run, filename)
        if not source.is_file() or not manifest.get(field) or digest(source) != manifest[field]:
            raise ValueError("Human review document source missing or unverified")
    source = safe_evidence_path(run, manifest["document_file"])
    if source.stat().st_size > 24_000:
        return {"status": "UNAVAILABLE", "reason": "SIZE_LIMIT", "file": source.name,
                "sha256": manifest["document_sha256"]}
    return {"status": "LINKED_DOCUMENT_NOT_APPROVAL", "file": source.name,
            "sha256": manifest["document_sha256"], "content": source.read_text(encoding="utf-8")}


def user_guidance_support(run):
    """Collect linked report previews, never publish or infer user receipt."""
    record_path = safe_evidence_path(run, "external_reporting.json")
    if not record_path.is_file():
        return {"status": "UNAVAILABLE", "reason": "NO_EXTERNAL_REPORTING_RECORD"}
    record = read(record_path)
    if not isinstance(record, dict) or record.get("run_id") != run.name:
        raise ValueError("User guidance run mismatch")
    for name, field in (("final_report.json", "final_report_sha256"),
                        ("checkpoint4.json", "checkpoint4_sha256")):
        path = safe_evidence_path(run, name)
        if not path.is_file() or not record.get(field) or digest(path) != record[field]:
            raise ValueError("User guidance report/checkpoint link unverified")
    report = read(safe_evidence_path(run, "final_report.json"))
    checkpoint = read(safe_evidence_path(run, "checkpoint4.json"))
    if report.get("run_id") != run.name or checkpoint.get("checkpoint") != "CP4":
        raise ValueError("User guidance report run/checkpoint mismatch")
    # CP4 has no run_id in P1's contract; the final report binds it by hash.
    if report.get("checkpoint4_sha256") != record["checkpoint4_sha256"]:
        raise ValueError("User guidance checkpoint differs from final report")
    result = {"status": "LINKED_RECORD", "run_id": run.name,
              "record_sha256": digest(record_path), "mode": record.get("mode"),
              "final_report_sha256": record["final_report_sha256"],
              "checkpoint4_sha256": record["checkpoint4_sha256"],
              "channels": {}}
    for channel in ("slack", "notion"):
        item = record.get(channel) or {}
        if not isinstance(item, dict):
            raise ValueError("Invalid user guidance channel record")
        entry = {"status_as_recorded": item.get("status"), "payload_available": False}
        result["channels"][channel] = entry
        name = item.get("payload_file")
        if not name:
            entry["reason"] = "NO_PAYLOAD_REFERENCE"
            continue
        # Only known report output types, not an arbitrary file named in a record.
        if name != channel + "_payload.json":
            raise ValueError("Unsupported user guidance payload reference")
        path = safe_evidence_path(run, name)
        if not path.is_file() or not item.get("payload_sha256") or digest(path) != item["payload_sha256"]:
            raise ValueError("User guidance payload missing or unverified")
        payload = read(path)
        if not isinstance(payload, dict):
            raise ValueError("Invalid user guidance payload")
        if channel == "notion":
            rows = payload.get("records")
            if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("run_id") != run.name for row in rows):
                raise ValueError("User guidance Notion run mismatch")
        elif run.name not in encode(payload):
            raise ValueError("User guidance Slack run missing")
        entry.update(payload_available=True, file=name, sha256=item["payload_sha256"], content=payload)
    return result


def collect_packets(run, work, spec, plan, checks):
    """Select role evidence; do not feed evaluator material back into P1."""
    docs = {}
    for original in sorted(run.rglob("*.json")):
        key = original.relative_to(run).as_posix()
        path = safe_evidence_path(run, key)
        # Do not turn an oversized/linked final artifact into 'not executed'.
        if path.stat().st_size >= 2_000_000:
            raise ValueError("Evaluation JSON read budget exceeded: " + key)
        docs[key] = read(path)
    if "request.json" in docs and docs["request.json"] != plan["request"]:
        raise ValueError("Evaluation request differs from saved P1 request")
    for key, value in docs.items():
        if isinstance(value, dict) and "run_id" in value and value["run_id"] != run.name:
            raise ValueError("Evaluation artifact belongs to another Run: " + key)
    # Compare with the saved run, never today's SRS or an inferred replacement.
    # Legacy evidence without hashes remains readable, explicitly unverified.
    manifest = docs.get("run_manifest.json", {})
    input_links = {}
    for name, file_field, hash_field in (
        ("request.json", "request_file", "request_sha256"),
        ("srs_snapshot.md", "srs_snapshot_file", "srs_sha256"),
    ):
        path = safe_evidence_path(run, name)
        if file_field in manifest and manifest[file_field] != name:
            raise ValueError("Saved input file reference mismatch: " + name)
        sha = digest(path) if path.is_file() else None
        if ((hash_field in manifest and (not sha or manifest[hash_field] != sha))
                or (file_field in manifest and not sha)):
            raise ValueError("Saved input missing or unverified: " + name)
        input_links[name] = {
            "status": "HASH_VERIFIED" if hash_field in manifest else
                      "UNVERIFIED_NO_SAVED_HASH" if sha else "UNAVAILABLE_NOT_SAVED",
            "sha256": sha,
        }
    srs_path = safe_evidence_path(run, "srs_snapshot.md")
    # The catalog used by A2, not today's possibly changed official registry.
    catalog_name = "approved_regression_catalog.json"
    catalog_hash = docs.get("agent2_manifest.json", {}).get("approved_regression_catalog_sha256")
    if catalog_hash and (catalog_name not in docs or digest(safe_evidence_path(run, catalog_name)) != catalog_hash):
        raise ValueError("Saved regression catalog missing or unverified")
    # New P1 transport omits the current SRS text from model-authored output.
    # Preserve old runs, but verify declared provenance before attributing it.
    agent2_manifest = docs.get("agent2_manifest.json", {})
    srs_records = agent2_manifest.get("srs_source_records")
    srs_ownership = None
    if srs_records is not None or agent2_manifest.get("prompt_version") == "agent2-2.62":
        if not isinstance(srs_records, list) or len(srs_records) not in (1, 2):
            raise ValueError("SRS source records missing or invalid")
        for attempt, entry in enumerate(srs_records, 1):
            name = f"agent2_srs_source_attempt_{attempt}.json"
            if not isinstance(entry, dict) or entry.get("file") != name:
                raise ValueError("SRS source record reference mismatch")
            record = docs.get(name)
            if (not isinstance(record, dict)
                    or digest(safe_evidence_path(run, name)) != entry.get("sha256")
                    or input_links["srs_snapshot.md"]["status"] != "HASH_VERIFIED"
                    or record.get("srs_sha256") != input_links["srs_snapshot.md"]["sha256"]):
                raise ValueError("SRS source record missing or unverified")
        srs_ownership = {
            "status": "HASH_VERIFIED",
            "records": srs_records,
            "boundary": "current_acceptance_criteria는 고정 SRS에서 프로그램이 붙인 원문입니다. "
                        "변경안·이유·요구사항 ID 선택은 모델 작성 대상입니다. "
                        "원문 연결 해시는 변경안의 의미 정확성이나 실제 호출 입력 확보를 증명하지 않습니다. "
                        "NOT_CAPTURED는 모델 원본 출력 미확보이며 모델이 작성하지 않았다는 뜻이 아닙니다.",
        }
    calls = []
    for path in sorted((work / "calls").glob("*/request.json")):
        calls.append(request_evidence(read(path), digest(path)))
    ledger_path = work / "call_ledger.json"
    ledger = read(ledger_path) if ledger_path.is_file() else {}
    tc_reference = (Path(__file__).resolve().parents[2] / "benchmarks/v2/TC_REFERENCE.md").read_text(encoding="utf-8")
    packets = {}
    code_references = []
    for key, manifest in docs.items():
        if Path(key).name != "agent3_manifest.json" or not manifest.get("candidate_file"):
            continue
        candidate_dir = Path(key).parent / "candidates"
        name = manifest["candidate_file"]
        safe_evidence_path(run / candidate_dir, name)
        code_references.append(((candidate_dir / name).as_posix(), manifest.get("candidate_sha256")))
    support = execution_support(run, work, docs.get("validation_execution.json", {}),
                                include_code=False,
                                code_references=code_references,
                                trial_documents=[(key, value, docs.get((Path(key).parent / "agent3_manifest.json").as_posix(), {}))
                                                 for key, value in docs.items() if Path(key).name == "agent3_trial.json"])
    for role in ROLES:
        artifact_keys = {"A1": ["agent1_change_analysis.json"],
                         "A2": ["agent2_test_design.json"],
                         "A3": [key for key in docs if key.endswith("agent3_automation_plan.json")],
                         "A4": ["agent4_analysis.json", "final_report.json"]}[role]
        artifact = {key: docs[key] for key in artifact_keys if key in docs}
        output_state = "FINAL_ARTIFACT_PRESENT" if artifact else "NO_FINAL_ARTIFACT"
        if not artifact and role in ("A1", "A2", "A3"):
            stem = {"A1": "agent1_change_analysis", "A2": "agent2_test_design",
                    "A3": "agent3_automation_plan"}[role]
            # Interrupted repair can leave valid drafts but no final handoff.
            # Keep them as drafts; never manufacture a final output or approval.
            artifact = {key: value for key, value in docs.items()
                        if Path(key).name.startswith(stem + "_attempt_")
                        and Path(key).stem.removeprefix(stem + "_attempt_").isdigit()}
            if artifact:
                output_state = "DRAFT_ONLY_NO_FINAL_HANDOFF"
        if role == "A3" and not artifact and "validation_execution.json" in docs:
            artifact = {"new_plan": "NOT_REQUIRED_OR_EXCLUDED",
                        "selection": docs.get("agent3_selection.json", {}),
                        "execution": docs["validation_execution.json"]}
        if not artifact:
            # Eligibility/selection/error is an output too, but not a plan.
            artifact = {key: value for key, value in docs.items()
                        if Path(key).name in {f"agent{role[-1]}_error.json", f"agent{role[-1]}_eligibility.json",
                                             f"agent{role[-1]}_selection.json", f"agent{role[-1]}_run_summary.json"}}
            if artifact:
                output_state = "STATUS_ONLY_NO_FINAL_ARTIFACT"
        if not artifact:
            continue
        role_calls = [call for call in calls if call["role"] == role]
        checkpoint_docs = {}
        review_input_prefix = f"agent{role[-1]}_grounding_input_attempt_"
        for key, value in docs.items():
            basename = Path(key).name
            shared = {"orchestrator_manifest.json"}
            if role in ("A1", "A2"):
                shared.add("run_manifest.json")
            if role in ("A3", "A4"):
                # Execution may stop before its manifest/report exists. Retain
                # the recorded cause, not just the missing-final-output state.
                shared.update({"validation_manifest.json", "validation_error.json"})
            if not (basename.startswith((f"checkpoint{role[-1]}", f"agent{role[-1]}_"))
                    or key in shared):
                continue
            stem = Path(key).stem
            if stem.startswith(review_input_prefix) and stem.removeprefix(review_input_prefix).isdigit():
                # These are P1's saved reference inputs, not review judgments.
                # Retain the catalog/fixed executor facts without sending the
                # much larger review instructions and duplicated output body.
                context = value.get("context", {}) if isinstance(value, dict) else {}
                retained = {field: context[field] for field in (
                    "catalog", "compiler_recovery_facts", "execution_interface", "compiler_plan_facts")
                    if field in context}
                value = {"payload_omitted": "GROUNDING_BODY_NOT_ROLE_GENERATION",
                         "raw_artifact_sha256": digest(run / key)}
                if retained:
                    value["retained_reference_context"] = retained
            checkpoint_docs[key] = value
        source = {
            "context": {"request": plan["request"], "independent_oracle": plan["oracle"],
                        "srs": srs_path.read_text(encoding="utf-8"), "saved_input_links": input_links,
                        "upstream": {key: docs[key] for key in (
                            ("agent1_change_analysis.json",) if role == "A2" else
                            ("agent2_test_design.json",) if role == "A3" else
                            ("validation_execution.json",) if role == "A4" else ()
                        ) if key in docs}},
            "artifact": artifact,
            "trace": {"output_state": output_state,
                      "model_call_records": [item for item in ledger.get("attempts", []) if item.get("role") == role],
                      "actual_model_requests": role_calls, "checkpoints_and_attempts": checkpoint_docs},
        }
        if role == "A2":
            source["context"]["user_tc_reference"] = tc_reference
        if role in ("A2", "A3") and srs_ownership is not None:
            source["context"]["srs_source_ownership"] = srs_ownership
        if role in ("A2", "A3"):
            catalog_content, catalog_omissions = catalog_evidence(docs.get(catalog_name))
            source["context"]["existing_tc_catalog"] = {
                "status": "HASH_VERIFIED" if catalog_hash else "UNVERIFIED" if catalog_name in docs else "UNAVAILABLE",
                "file": catalog_name, "sha256": catalog_hash,
                "content": catalog_content,
                "scope": "해당 실행 당시 저장한 승인 TC와 기본 TC 상세 명세(포함된 경우). 현재 자산으로 대체하지 않으며, 상세 명세는 실행·복원 성공 증거가 아님"}
            if catalog_omissions:
                source["context"]["existing_tc_catalog"]["local_only_metadata"] = catalog_omissions
        if role == "A4":
            source["artifact"]["user_guidance"] = user_guidance_support(run)
            source["artifact"]["human_review_document"] = human_review_support(run)
            source["trace"]["evaluation_boundary"] = (
                "최종 보고 JSON과 같은 실행의 user_guidance 안내를 함께 평가하세요. "
                "PREVIEW/DRY_RUN은 작성된 안내이지 외부 발송·수신·공식 승인 증거가 아닙니다. "
                "status_as_recorded는 원본 전송 기록이며 이 평가기가 수신을 독립 확인한 것이 아닙니다. "
                "안내 자료가 없으면 전체 사용자 안내가 없다고 단정하지 말고 해당 판단을 보류하세요. "
                "확인 목록이 비어 있다는 사실만으로 감점하지 말고 실제 누락 조건/후속 행동을 대조하세요. "
                "P2 평가 결과에 대한 사람 검토와 P1 시험 결과의 자산 승인 절차를 구분하세요.")
        if role == "A3":
            source["inventory"] = {key: v for key, v in docs.items() if key.endswith("ui_observation.json")}
            source["trace"]["code_artifacts"] = {
                name: entry for name, entry in support["files"].items()
                if Path(name).suffix.lower() == ".py"}
            source["trace"]["evaluation_boundary"] = (
                "자동 생성 코드 본문은 AI 평가 입력이 아닙니다. A3-03은 프로그램이 계획과 구조화 관찰 기록을 대조합니다. "
                "코드가 전송되지 않았다는 이유로 실행 확인이 없다고 단정하지 마세요. "
                "AI는 제공된 TC·계획·실행 증거의 의미와 주장 범위만 평가하며, 확인할 증거가 없으면 보류합니다.")
        if role in ("A3", "A4"):
            source["execution"] = {key: v for key, v in docs.items()
                                   if key.endswith("agent3_trial.json") or key == "validation_execution.json"}
            if source["execution"]:
                source["execution"]["verified_support"] = support
            else:
                source["trace"]["execution_support_unavailable"] = support["omissions"]
        omitted = []
        # Explicit delivery inventory: new/unmapped JSON cannot disappear
        # silently. This is availability metadata, never a PASS/FAIL grader.
        selected = set(artifact_keys) & docs.keys() | set(artifact) & docs.keys() | set(checkpoint_docs)
        selected |= set(source["context"]["upstream"])
        selected |= set(source.get("inventory", {})) | set(source.get("execution", {}))
        selected.add("request.json")
        if role in ("A2", "A3"):
            selected.add(catalog_name)
        if role == "A4":
            selected.update(entry["file"] for entry in
                            source["artifact"]["user_guidance"].get("channels", {}).values()
                            if entry.get("payload_available"))
        source["trace"]["evidence_delivery"] = {
            "json_files": {key: ("SELECTED_REFERENCE_CONTEXT" if isinstance(checkpoint_docs.get(key), dict)
                                  and checkpoint_docs[key].get("retained_reference_context") else
                                  "HASH_ONLY_REVIEW_INPUT" if isinstance(checkpoint_docs.get(key), dict)
                                  and checkpoint_docs[key].get("payload_omitted") else
                                  "VERIFIED_GUIDANCE_PROJECTION" if role == "A4" and key in {
                                      "external_reporting.json", "사람_최종_검토_manifest.json"} else
                                  "VERIFIED_CATALOG_PROJECTION" if key == catalog_name and role in ("A2", "A3") and catalog_omissions else
                                  "ROLE_INPUT" if key in selected else "LOCAL_ONLY_NOT_ROLE_INPUT")
                           for key in docs},
            "actual_model_requests": "CAPTURED" if role_calls else "UNAVAILABLE_NOT_PROOF_OF_NO_CALL",
            "boundary": "파일 포함은 평가 완료가 아닙니다. 코드 본문·HTML·이미지·Trace ZIP·승인 이력은 로컬 보존 자료이며 AI 의미 평가 입력이 아닙니다. 기본 TC 상세는 baseline_details가 실제 저장된 경우에만 확인하며 과거 실행에 소급하지 않습니다."}
        source = judge_projection(source, work, omitted)
        source["trace"]["metadata_path_replacements"] = omitted
        source = compact_evidence(source)
        source["reference_protocol"] = (
            "context와 artifact는 직접 대조할 원문이며 참조로 대체하지 않습니다. "
            "다른 자료의 evidence_reference는 sources 루트에서 시작하는 동일 내용의 최초 위치입니다. "
            "evidence_text_parts는 문자열과 참조 원문을 순서대로 이어 붙이면 원래 요청문이 되며 raw_utf8_sha256은 원문 해시입니다. "
            "인용은 참조가 가리키는 실제 값의 source_id와 path를 사용합니다. "
            "parsed_json은 실제 요청 JSON의 파싱 값이며 원본 문자열 해시는 raw_utf8_sha256입니다. "
            "payload_omitted인 근거 검토 요청은 본문 대신 원본 파일 해시·호출 메타데이터를 제공합니다. "
            "저장 검토 입력의 기존 TC 목록·실행기 복원 사실·실행 인터페이스·계획 사실은 retained_reference_context로 원문 보존하며, 검토 지침과 중복 본문은 제외합니다. 이 설명은 실제 실행 성공 증거가 아닙니다. "
            "모델 호출 기록과 Checkpoint·재작성·검토 결과는 유지하지만 제외된 요청 본문의 의미 평가는 하지 않습니다. "
            "원본 요청 바이트와 중복 없는 모든 증거 값은 로컬 압축 증거에 보존됩니다."
        )
        packet = JudgePacket(
            case_id=spec.case_id, trial_id=run.name, agent_id=role,
            target_version=plan["target_commit"], sources=source,
            rubric=[r for r in role_items(role) if r["criterion_id"] not in PYTHON_CRITERIA],
            rubric_profile=RUBRIC_VERSION,
            oracle_status=plan["oracle"]["status"], scoring_profile=SCORE_VERSION,
            code_checks=[c for c in checks if c["rule"].startswith((role + ".", "C01."))],
        )
        packets[role] = packet
    return packets


def run_flow(target, spec, config_root, output_root, approval_sha256, *, worker=None, judge=None):
    info = make_preview(target, spec, config_root)
    if info["approval_sha256"] != approval_sha256:
        raise ValueError("Execution requires approval bound to this exact preview")
    output_root = output_root.resolve()
    target = target.resolve()
    if output_root.is_relative_to(target) or target.is_relative_to(output_root):
        raise ValueError("Output and target must not overlap")
    if (worker is None) != (judge is None):
        raise ValueError("Test execution requires both injected worker and judge")
    test_mode = worker is not None
    if not test_mode:
        clean_environment(include_key=True)
    worker = worker or run_worker
    judge = judge or judge_evaluate
    plan = info["plan"]
    root = output_root / ("FLOW-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6])
    root.mkdir(parents=True, exist_ok=False)
    write(root / "preview.json", info)
    result = dict(mode="TEST_DOUBLE" if test_mode else "FULL_FLOW_PILOT", status="RUNNING",
                  planned_trials=spec.trials, generation_attempts=0, judge_attempts=0, judge_slots_used=0,
                  quality_score=None, calibration="NOT_VALIDATED", trials=[], stop_reason=None)
    write(root / "result.json", result)
    seen_response_ids = set()
    for index in range(spec.trials):
        row = {"trial": index + 1, "status": "NOT_EXECUTED", "roles": {r: "NOT_EXECUTED" for r in ROLES}}
        result["trials"].append(row)
        if result["stop_reason"]:
            row["reason"] = result["stop_reason"]
            write(root / "result.json", result)
            continue
        work = None
        try:
            fresh = make_preview(target, spec, config_root)
            if fresh["approval_sha256"] != approval_sha256:
                raise ValueError("Pinned input or evaluator changed between trials")
            remaining = spec.generation_calls_total - result["generation_attempts"]
            if remaining < 1:
                result["stop_reason"] = row["reason"] = "GENERATION_BUDGET_EXHAUSTED"
                continue
            work = Path(tempfile.mkdtemp(prefix="qaf-"))
            row["workspace"] = str(work)
            copy = work / "p1"
            for name, expected in plan["runtime_hashes"].items():
                source = child(target, name)
                if digest(source) != expected:
                    raise ValueError("Source changed before copy")
                dest = child(copy, name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest)
                if digest(dest) != expected:
                    raise ValueError("Copy hash mismatch")
            run_id = "RUN-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex[:6].upper()
            row["run_id"] = run_id
            config = dict(spec.model_dump(), run_id=run_id,
                          generation_limit=min(remaining, spec.generation_calls_per_trial),
                          seen_response_ids=sorted(seen_response_ids))
            row["status"] = "STARTED"
            write(root / "result.json", result)
            outcome = worker(work, config)
            ledger = read(work / "call_ledger.json") if (work / "call_ledger.json").is_file() else None
            if ledger is None or not isinstance(ledger.get("attempts"), list):
                raise ValueError("Missing generation accounting; further trials blocked")
            attempts = ledger["attempts"]
            result["generation_attempts"] += len(attempts)
            if len(attempts) > config["generation_limit"]:
                raise ValueError("Generation budget exceeded")
            duplicate = False
            for item in attempts:
                rid = item.get("response_id")
                if rid:
                    duplicate |= rid in seen_response_ids
                    seen_response_ids.add(rid)
            if duplicate:
                outcome.update(status="ERROR", reason="DUPLICATE_RESPONSE_ID")
            run = copy / "runs" / run_id
            row["roles"], row["new_plan_not_required"] = stage_states(run, outcome)
            row["execution"] = outcome
            row["waiting_for_user"] = waiting_user_notice(run, outcome)
            row["status"] = outcome["status"]
            row["generation_attempts"] = len(attempts)
            row["generation_http_attempts"] = len(ledger.get("http_requests", []))
            row["generation_responses_received"] = sum(a.get("status") == "RECEIVED" for a in attempts)
            source_ok = runtime_unchanged(target, plan["runtime_hashes"])
            copy_ok = runtime_unchanged(copy, plan["runtime_hashes"])
            if (outcome.get("reason") == "RUNTIME_INPUT_PREFLIGHT_FAILED"
                    and not attempts and not outcome.get("stages")):
                # Preserve setup integrity evidence, but do not grade an Agent
                # that never ran. The finally block still archives this trial.
                row["runtime_input_integrity"] = {
                    "source_unchanged": source_ok, "copy_unchanged": copy_ok}
                result["stop_reason"] = outcome["reason"]
                continue
            row["checks"] = local_checks(run, source_ok, copy_ok)
            row["artifact_hashes"] = inventory(run) if run.exists() else {}
            comparison = comparison_snapshot(run)
            write(root / f"trial-{index + 1}" / "comparison.json", comparison)
            row["comparison_fingerprints"] = {role: item["fingerprint"] for role, item in comparison.items()}
            if not source_ok or not copy_ok:
                result["stop_reason"] = "ASSET_PROTECTION_FAILED"
                row["status"] = "ERROR"
            if outcome["status"] == "ERROR":
                result["stop_reason"] = outcome.get("reason", "EXECUTION_ERROR")
            row["judges"] = {}
            row["python_evaluation"] = python_scores(run, row["checks"])
            observed = {}
            observation_grade = grade_execution_observations(run, observations=observed)
            row["product_expectation"] = classify_product_expectation(
                spec.expected_product_mismatches, observed, observation_grade["verdict"])
            packets = collect_packets(run, work, spec, plan, row["checks"])
            prepared = {}
            available_slots = spec.judge_calls_total - result["judge_slots_used"]
            preflight_failed = False
            # Prepare the entire selected evaluation batch before any paid
            # judge. Unselected roles stay unscored, not preparation failures.
            for index_in_batch, (role, packet) in enumerate(packets.items()):
                destination = root / f"trial-{index + 1}" / role
                write(destination / "packet.json", packet.model_dump())
                if index_in_batch >= available_slots:
                    row["judges"][role] = {"status": "NOT_EVALUATED", "reason": "JUDGE_BUDGET_EXHAUSTED"}
                    continue
                if result["stop_reason"]:
                    row["judges"][role] = {"status": "NOT_EVALUATED", "reason": "SAFETY_STOP"}
                    continue
                try:
                    preview = judge_preview(packet, spec.judge_model)
                    write(destination / "preview.json", preview)
                    prepared[role] = (packet, preview, destination)
                except Exception as exc:
                    row["judges"][role] = {"status": "ERROR", "reason": type(exc).__name__}
                    preflight_failed = True
            if preflight_failed:
                result["stop_reason"] = "JUDGE_PREFLIGHT_FAILED"
            if inventory(run) != row["artifact_hashes"]:
                result["stop_reason"] = "EVIDENCE_CHANGED_DURING_REVIEW"
                row["status"] = "ERROR"
            for role, (packet, preview, destination) in prepared.items():
                try:
                    if result["stop_reason"]:
                        row["judges"][role] = {"status": "NOT_EVALUATED", "reason": "SAFETY_STOP"}
                        continue
                    result["judge_slots_used"] += 1
                    row["judges"][role] = {"status": "STARTED", "server_completion": "UNKNOWN"}
                    write(root / "result.json", result)
                    judged = judge(packet, spec.judge_model, preview["approval_sha256"], destination)
                    verdict = read(judged / "result.json")
                    result["judge_attempts"] += verdict.get("sdk_parse_attempts", 0)
                    response_id = verdict.get("response_id")
                    if response_id and response_id in seen_response_ids:
                        verdict.update(status="ERROR", assessment="INCOMPLETE", reason="DUPLICATE_JUDGE_RESPONSE")
                    if response_id:
                        seen_response_ids.add(response_id)
                    row["judges"][role] = verdict
                    if verdict.get("status") == "ERROR":
                        result["stop_reason"] = "JUDGE_ERROR"
                except Exception as exc:
                    row["judges"][role] = {"status": "ERROR", "reason": type(exc).__name__}
                    result["stop_reason"] = "JUDGE_ERROR"
            if inventory(run) != row["artifact_hashes"]:
                result["stop_reason"] = "EVIDENCE_CHANGED_DURING_REVIEW"
                row["status"] = "ERROR"
            row["assessment"] = (
                "ISSUES_FOUND" if any(c["status"] == "FAIL" for c in row["checks"])
                or any(j["verdict"] == "FAIL" for j in row["python_evaluation"]["judgments"])
                or any(j.get("assessment") == "ISSUES_FOUND" for j in row["judges"].values())
                else "REVIEW"
            )
        except Exception as exc:
            row.update(status="ERROR", reason=type(exc).__name__)
            result["stop_reason"] = "INCOMPLETE_OR_UNSAFE_TRIAL"
        finally:
            from .detailed_rubric import full_scorecard
            row["scorecard"] = full_scorecard(row.get("judges", {}), row.get("python_evaluation"))
            if work:
                # Archive original bytes without rewriting P1 manifests or their paths.
                try:
                    row["evidence_archive_sha256"] = archive_evidence(work, root / f"trial-{index + 1}" / "evidence.zip")
                    row["evidence_archive"] = f"trial-{index + 1}/evidence.zip"
                except Exception as exc:
                    row["archive_error"] = type(exc).__name__
                    result["stop_reason"] = "EVIDENCE_ARCHIVE_FAILED"
                    row["status"] = "ERROR"
                write(root / f"trial-{index + 1}" / "record.json", row)
            write(root / "result.json", result)
    result["status"] = "STOPPED" if result["stop_reason"] else "COMPLETED"
    result["role_reached"] = {r: sum(t["roles"][r] != "NOT_EXECUTED" for t in result["trials"]) for r in ROLES}
    result["quality_assessment"] = "REVIEW_REQUIRED"
    # Separate from COMPLETED/STOPPED: did the product fail exactly as declared?
    statuses = [t.get("product_expectation", {}).get("status", "NOT_EVALUATED") for t in result["trials"]]
    result["product_expectation"] = {status: statuses.count(status) for status in sorted(set(statuses))}
    result["comparison"] = {
        "method": "STRUCTURED_AND_TEXT_FINGERPRINT_NOT_SEMANTIC_EQUIVALENCE",
        "same_wrong_not_ruled_out": True,
        "roles": {role: {
            "available_trials": sum(role in t.get("comparison_fingerprints", {}) for t in result["trials"]),
            "distinct_outputs": len({t["comparison_fingerprints"][role] for t in result["trials"]
                                     if role in t.get("comparison_fingerprints", {})}),
            "planned_trials": spec.trials,
        } for role in ROLES},
    }
    result["api_request_attempts"] = 0 if test_mode else result["generation_attempts"] + result["judge_attempts"]
    from .detailed_rubric import full_scorecard, repeat_scores
    result["score_comparison"] = repeat_scores([
        full_scorecard(t.get("judges", {}), t.get("python_evaluation")) for t in result["trials"]])
    write(root / "result.json", result)
    return root
