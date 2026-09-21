"""Version-pinned P1 execution, collection and separate role evaluation.

Preview is read-only. Execution requires an exact plan hash and an explicit
call budget. A fresh working copy is not an OS security sandbox.
"""
from __future__ import annotations

from collections import Counter
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

from .detailed_rubric import role_items, VERSION as RUBRIC_VERSION
from .openai_judge import JudgePacket, encode, preview as judge_preview, evaluate as judge_evaluate, scan_payload
from .v2_recorded import child, digest, read, write, target_pin, inventory, execution_rows

VERSION = "full-flow-0.1"
ROLES = ("A1", "A2", "A3", "A4")


class FlowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,60}$")
    request: str
    oracle: str
    generation_model: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    judge_model: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,100}$")
    trials: Literal[1, 5] = 1
    max_candidates: int = Field(default=3, ge=1, le=10)
    generation_calls_per_trial: int = Field(default=10, ge=1, le=24)
    generation_calls_total: int = Field(default=10, ge=1, le=120)
    judge_calls_total: int = Field(default=4, ge=0, le=20)
    trial_timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    product_timeout_seconds: int = Field(default=90, ge=10, le=180)
    generation_max_output_tokens: int = Field(default=12000, ge=2000, le=24000)


def p2_pin():
    root = Path(__file__).resolve().parents[2]
    files = list((root / "src/eval_harness").rglob("*.py"))
    files += [root / "benchmarks/v2/TC_REFERENCE.md"]
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(files)}


def selected_files(target, request, pin):
    required = {
        "docs/01_PRODUCT_SRS.md", "product_baseline/virtual-controller.html",
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
        "generation_destination": "https://api.openai.com/v1/responses",
        "judge_destination": "https://api.openai.com/v1/responses",
        "generation_sdk_retries": 0, "judge_sdk_retries": 0,
        "checkpoint_rewrites": "P1 existing bounded policy; every parse attempt is charged",
        "external_publish": False, "official_registration": False,
        "maximum_api_attempts": spec.generation_calls_total + spec.judge_calls_total,
        "scope": "A1-A4 + checkpoint artifacts + partial E2E code checks",
        "transmission": {
            "generation": ["request", "related SRS", "existing TC catalog", "actual upstream outputs",
                           "limited UI observation", "checkpoint repair feedback"],
            "judge": ["request/SRS", "independent oracle", "user TC writing criteria",
                      "role outputs", "request/repair traces", "plan/code", "neutral execution/report"],
            "excluded": ["credentials", "absolute local paths", "full HTML", "screenshots", "trace archives"],
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
    a1 = files.get("agent1_change_analysis.json")
    if not report or not execution:
        add("E01.COMPLETION", "NOT_EVALUATED", "최종 실행·보고 자료 미도달; 완료로 판단하지 않음")
        return checks
    rows = execution_rows(execution)
    actual = Counter(row["status"] for row in rows)
    count_ok = report.get("total_results") == len(rows) and all(
        report.get("status_counts", {}).get(key, 0) == actual.get(key, 0)
        for key in set(actual) | set(report.get("status_counts", {}))
    )
    add("A4.COUNTS", "PASS" if count_ok else "FAIL", "실행 행을 독립 재집계해 보고 합계와 대조")
    analysis = files.get("agent4_analysis.json", {})
    add("C01.REPORT_CHAIN", "PASS" if (
        analysis.get("validation_execution_sha256") == digest(run / "validation_execution.json")
        and report.get("analysis_sha256") == digest(run / "agent4_analysis.json")
    ) else "FAIL", "실제 실행→분석→최종 보고의 파일 해시 연결")
    if a1 is not None:
        gaps = a1.get("information_gaps", [])
        questions = a1.get("user_questions", [])
        add("E02.GAPS", "PASS" if all(x in report.get("제외된_정보_부족", []) for x in gaps) else "FAIL",
            "분석의 미정 항목이 최종 보고에 보존됐는지 대조")
        add("E03.QUESTIONS", "PASS" if all(x in report.get("최종_확인_사항", []) for x in questions) else "FAIL",
            "사용자 확인 질문 원문이 최종 보고에 보존됐는지 대조")
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
    plans = sorted(run.glob("agent3_candidates/*/agent3_automation_plan.json"))
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


def compact_evidence(source):
    """Lossless value deduplication. References point to earlier source paths."""
    seen = {}
    def visit(value, path):
        encoded = encode(value)
        if len(encoded.encode("utf-8")) >= 512:
            if encoded in seen:
                return {"evidence_reference": seen[encoded]}
            seen[encoded] = path
        if isinstance(value, dict):
            return {k: visit(v, path + [k]) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v, path + [str(i)]) for i, v in enumerate(value)]
        return value
    return visit(source, [])


def request_evidence(call):
    """Expose JSON user inputs as values; raw request bytes stay in the ZIP."""
    result = json.loads(encode(call))
    for message in result.get("payload", {}).get("input", []):
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.lstrip().startswith(("{", "[")):
            try:
                message["content"] = {"parsed_json": json.loads(content),
                                      "raw_utf8_sha256": sha256(content.encode()).hexdigest()}
            except ValueError:
                pass
    return result


def collect_packets(run, work, spec, plan, checks):
    """Select role evidence; do not feed evaluator material back into P1."""
    docs = {p.relative_to(run).as_posix(): read(p) for p in sorted(run.rglob("*.json"))
            if p.stat().st_size < 2_000_000 and not p.is_symlink()}
    calls = []
    for path in sorted((work / "calls").glob("*/request.json")):
        calls.append(request_evidence(read(path)))
    tc_reference = (Path(__file__).resolve().parents[2] / "benchmarks/v2/TC_REFERENCE.md").read_text(encoding="utf-8")
    packets = {}
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
            continue
        role_calls = [call for call in calls if call["role"] == role]
        source = {
            "context": {"request": plan["request"], "independent_oracle": plan["oracle"],
                        "srs": (run / "srs_snapshot.md").read_text(encoding="utf-8"),
                        "upstream": {key: docs[key] for key in (
                            ("agent1_change_analysis.json",) if role == "A2" else
                            ("agent2_test_design.json",) if role == "A3" else
                            ("validation_execution.json",) if role == "A4" else ()
                        ) if key in docs}},
            "artifact": artifact,
            "trace": {"output_state": output_state,
                      "actual_model_requests": role_calls, "checkpoints_and_attempts": {
                key: value for key, value in docs.items()
                if (f"checkpoint{role[-1]}" in key or f"agent{role[-1]}_" in key)
                and ("attempt" in key or "checkpoint" in key or "manifest" in key)
            }},
        }
        if role == "A2":
            source["context"]["user_tc_reference"] = tc_reference
        if role == "A3":
            source["inventory"] = {key: v for key, v in docs.items() if key.endswith("ui_observation.json")}
            source["trace"]["compiled_code"] = {
                p.relative_to(run).as_posix(): p.read_text(encoding="utf-8")
                for p in sorted(run.glob("agent3_candidates/*/candidates/*.py"))}
        if role in ("A3", "A4"):
            source["execution"] = {key: v for key, v in docs.items()
                                   if key.endswith("agent3_trial.json") or key == "validation_execution.json"}
        omitted = []
        source = judge_projection(source, work, omitted)
        source["trace"]["metadata_path_replacements"] = omitted
        source = compact_evidence(source)
        source["reference_protocol"] = (
            "evidence_reference는 sources 루트에서 시작하는 동일 내용의 최초 위치입니다. "
            "인용은 참조가 가리키는 실제 값의 source_id와 path를 사용합니다. "
            "parsed_json은 실제 요청 JSON의 파싱 값이며 원본 문자열 해시는 raw_utf8_sha256입니다. "
            "원본 요청 바이트와 중복 없는 모든 증거 값은 로컬 압축 증거에 보존됩니다."
        )
        packet = JudgePacket(
            case_id=spec.case_id, trial_id=run.name, agent_id=role,
            target_version=plan["target_commit"], sources=source, rubric=role_items(role),
            rubric_profile=RUBRIC_VERSION, oracle_status=plan["oracle"]["status"],
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
            row["status"] = outcome["status"]
            row["generation_attempts"] = len(attempts)
            row["generation_http_attempts"] = len(ledger.get("http_requests", []))
            row["generation_responses_received"] = sum(a.get("status") == "RECEIVED" for a in attempts)
            source_ok = runtime_unchanged(target, plan["runtime_hashes"])
            copy_ok = runtime_unchanged(copy, plan["runtime_hashes"])
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
            for role, packet in collect_packets(run, work, spec, plan, row["checks"]).items():
                destination = root / f"trial-{index + 1}" / role
                write(destination / "packet.json", packet.model_dump())
                try:
                    preview = judge_preview(packet, spec.judge_model)
                    write(destination / "preview.json", preview)
                    if result["judge_slots_used"] >= spec.judge_calls_total:
                        row["judges"][role] = {"status": "NOT_EVALUATED", "reason": "JUDGE_BUDGET_EXHAUSTED"}
                        continue
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
                or any(j.get("assessment") == "ISSUES_FOUND" for j in row["judges"].values())
                else "REVIEW"
            )
        except Exception as exc:
            row.update(status="ERROR", reason=type(exc).__name__)
            result["stop_reason"] = "INCOMPLETE_OR_UNSAFE_TRIAL"
        finally:
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
    write(root / "result.json", result)
    return root
