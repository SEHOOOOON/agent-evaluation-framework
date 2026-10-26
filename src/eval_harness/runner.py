from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .fixtures import FIXTURE_EVALUATORS, now_iso, source_fingerprint, validate_source
from .models import (
    CheckResult,
    CheckStatus,
    GateDecision,
    RunManifest,
    RunSummary,
    Severity,
    StageResult,
    StageStatus,
)
from .reporting import export_html
from .security import redact_data
from .storage import ArtifactStore, atomic_write_json, read_json


# V1 fixture repository checked out as a sibling of this repository.
DEFAULT_PROJECT1_ROOT = Path(__file__).resolve().parents[3] / "portfolio_export"
DEFAULT_RUNS_ROOT = Path(__file__).resolve().parents[2] / "evaluation-runs"
DEFAULT_KNOWN_GOOD_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "known_good" / "project1"


def make_run_id() -> str:
    stamp = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d-%H%M%S")
    return f"EVAL-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def summarize(stages: list[StageResult]) -> RunSummary:
    checks = [check for stage in stages for check in stage.checks]
    findings = [finding for stage in stages for finding in stage.findings]
    return RunSummary(
        checks_passed=sum(check.status == CheckStatus.PASS for check in checks),
        checks_failed=sum(check.status == CheckStatus.FAIL for check in checks),
        checks_abstained=sum(check.status == CheckStatus.ABSTAIN for check in checks),
        checks_not_applicable=sum(check.status == CheckStatus.NOT_APPLICABLE for check in checks),
        checks_error=sum(check.status == CheckStatus.ERROR for check in checks),
        critical_findings=sum(finding.severity == Severity.CRITICAL for finding in findings),
        high_findings=sum(finding.severity == Severity.HIGH for finding in findings),
        total_findings=len(findings),
    )


def gate_stage(
    stages: list[StageResult],
    store: ArtifactStore,
    source_unchanged: bool,
) -> tuple[StageResult, GateDecision]:
    started = now_iso()
    blocking_findings = [
        finding
        for stage in stages
        for finding in stage.findings
        if finding.severity in {Severity.CRITICAL, Severity.HIGH}
    ]
    blocking_checks = [
        f"{stage.stage_id}:{check.criterion_id}"
        for stage in stages
        for check in stage.checks
        if check.status == CheckStatus.FAIL
        and check.severity in {Severity.CRITICAL, Severity.HIGH}
    ]
    failed_stages = [
        stage.stage_id for stage in stages if stage.status in {StageStatus.FAIL, StageStatus.ERROR}
    ]
    grader_errors = [
        f"{stage.stage_id}:{check.criterion_id}"
        for stage in stages
        for check in stage.checks
        if check.status == CheckStatus.ERROR
    ]
    reviews = sorted(
        {
            *[stage.stage_id for stage in stages if stage.status == StageStatus.REVIEW],
            *[
                stage.stage_id
                for stage in stages
                if any(check.status == CheckStatus.ABSTAIN for check in stage.checks)
            ],
        }
    )

    if blocking_findings or blocking_checks or failed_stages or grader_errors or not source_unchanged:
        decision = GateDecision.HOLD
        reason = "Critical/High 실패, 평가 오류 또는 원본 변경이 있어 현재 구성요소 버전을 승인할 수 없습니다."
    elif reviews:
        decision = GateDecision.HUMAN_REVIEW
        reason = "자동 판정이 불충분한 항목이 있어 Human Review가 필요합니다."
    else:
        decision = GateDecision.PASS
        reason = "정의된 평가 Gate를 모두 통과했습니다."

    gate_ref = store.add_json(
        "GATE-DECISION",
        {
            "evaluation_release_decision": decision.value,
            "blocking_finding_ids": [finding.finding_id for finding in blocking_findings],
            "blocking_check_ids": blocking_checks,
            "failed_stage_ids": failed_stages,
            "grader_error_ids": grader_errors,
            "review_stage_ids": reviews,
            "source_unchanged": source_unchanged,
            "reason": reason,
        },
    )
    gate_status = (
        CheckStatus.PASS
        if decision == GateDecision.PASS
        else CheckStatus.ABSTAIN
        if decision == GateDecision.HUMAN_REVIEW
        else CheckStatus.FAIL
    )
    check = CheckResult(
        criterion_id="PIPE-GATE-001",
        title="Evaluation Release Gate",
        status=gate_status,
        severity=Severity.CRITICAL,
        reason=reason,
        expected={
            "critical_or_high_open_findings": 0,
            "critical_or_high_failed_checks": 0,
            "failed_stages": 0,
            "grader_errors": 0,
            "source_unchanged": True,
        },
        actual={
            "blocking_findings": len(blocking_findings),
            "blocking_checks": blocking_checks,
            "failed_stages": failed_stages,
            "grader_errors": grader_errors,
            "review_stages": reviews,
            "source_unchanged": source_unchanged,
        },
        evidence_refs=[gate_ref],
    )
    status = (
        StageStatus.PASS
        if decision == GateDecision.PASS
        else StageStatus.REVIEW
        if decision == GateDecision.HUMAN_REVIEW
        else StageStatus.FAIL
    )
    return (
        StageResult(
            stage_id="pipeline_gate",
            step=5,
            agent_name="Pipeline · 최종 평가 판정",
            agent_mode="결정론적 Evaluation Gate",
            component_type="pipeline_gate",
            sut_executed=False,
            checkpoint_id="Final Gate",
            checkpoint_status=decision.value,
            status=status,
            mode_note="제품 릴리즈 판정과 구분되는 평가 대상 구성요소의 적용 판정입니다.",
            input_refs=[gate_ref],
            raw_output_ref=gate_ref,
            normalized_output_ref=gate_ref,
            checks=[check],
            findings=[],
            started_at=started,
            completed_at=now_iso(),
        ),
        decision,
    )


class EvaluationRunner:
    def __init__(
        self,
        source_root: Path = DEFAULT_PROJECT1_ROOT,
        runs_root: Path = DEFAULT_RUNS_ROOT,
    ) -> None:
        self.source_root = source_root.resolve()
        self.runs_root = runs_root.resolve()
        if (
            self.source_root == self.runs_root
            or self.source_root in self.runs_root.parents
            or self.runs_root in self.source_root.parents
        ):
            raise ValueError("평가 결과 경로는 Project1 원본 경로와 분리해야 합니다.")
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def _run_root(self, run_id: str) -> Path:
        if not re.fullmatch(r"EVAL-\d{8}-\d{6}-[A-F0-9]{6}", run_id):
            raise ValueError("올바르지 않은 Run ID입니다.")
        return self.runs_root / run_id

    def _save(self, manifest: RunManifest) -> None:
        atomic_write_json(
            self._run_root(manifest.run_id) / "manifest.json",
            manifest.model_dump(mode="json"),
        )

    def _event(self, run_id: str, event_type: str, **payload: object) -> None:
        path = self._run_root(run_id) / "events.jsonl"
        record = redact_data({"event": event_type, "at": now_iso(), "run_id": run_id, **payload})
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def create_manifest(
        self,
        suite: str = "project1-audit",
        execution_mode: str = "fixture",
        target_version: str = "project1-current",
        trials: int = 1,
    ) -> RunManifest:
        if suite not in {"project1-audit", "known-good"}:
            raise ValueError("MVP는 project1-audit과 known-good suite만 지원합니다.")
        if execution_mode != "fixture":
            raise ValueError("MVP는 fixture 모드만 지원합니다.")
        if trials != 1:
            raise ValueError("MVP fixture 모드는 trial=1만 지원합니다.")
        missing = validate_source(self.source_root)
        if missing:
            raise FileNotFoundError("Project1 fixture 누락: " + ", ".join(missing))
        run_id = make_run_id()
        run_root = self._run_root(run_id)
        run_root.mkdir(parents=True, exist_ok=False)
        before_hash = source_fingerprint(self.source_root)
        manifest = RunManifest(
            run_id=run_id,
            status="RUNNING",
            suite=suite,
            execution_mode=execution_mode,
            sut_executed=False,
            stability_evaluated=False,
            trial_count=trials,
            target_version=target_version,
            source_name=self.source_root.name,
            source_hash_before=before_hash,
            started_at=now_iso(),
        )
        self._save(manifest)
        self._event(run_id, "run_started", suite=suite, execution_mode=execution_mode)
        return manifest

    def _finalize_source_integrity(self, manifest: RunManifest) -> None:
        manifest.source_hash_after = source_fingerprint(self.source_root)
        manifest.source_unchanged = manifest.source_hash_before == manifest.source_hash_after

    def execute(self, manifest: RunManifest) -> RunManifest:
        store = ArtifactStore(self._run_root(manifest.run_id))
        try:
            for evaluator in FIXTURE_EVALUATORS:
                stage_name = evaluator.__name__.replace("evaluate_", "")
                self._event(manifest.run_id, "stage_started", stage_id=stage_name)
                stage = evaluator(self.source_root, store)
                manifest.stages.append(stage)
                manifest.summary = summarize(manifest.stages)
                self._save(manifest)
                self._event(
                    manifest.run_id,
                    "stage_completed",
                    stage_id=stage.stage_id,
                    status=stage.status.value,
                    finding_count=len(stage.findings),
                )

            self._finalize_source_integrity(manifest)
            gate, decision = gate_stage(
                manifest.stages,
                store,
                source_unchanged=bool(manifest.source_unchanged),
            )
            manifest.stages.append(gate)
            manifest.release_decision = decision
            manifest.summary = summarize(manifest.stages)
            self._event(manifest.run_id, "gate_decided", decision=decision.value)
            manifest.status = "COMPLETED"
            manifest.completed_at = now_iso()
            export_html(manifest, self._run_root(manifest.run_id) / "report.html")
            manifest.report_available = True
            self._save(manifest)
            self._event(manifest.run_id, "report_ready", file="report.html")
            self._event(manifest.run_id, "run_completed", decision=decision.value)
            return manifest
        except Exception as exc:
            manifest.status = "ERROR"
            manifest.error = f"{type(exc).__name__}: {exc}"
            manifest.completed_at = now_iso()
            try:
                self._finalize_source_integrity(manifest)
            except Exception:
                manifest.source_unchanged = False
            self._save(manifest)
            self._event(manifest.run_id, "run_failed", error=manifest.error)
            return manifest

    def run(self, **kwargs: object) -> RunManifest:
        return self.execute(self.create_manifest(**kwargs))

    def load(self, run_id: str) -> RunManifest:
        return RunManifest.model_validate(
            read_json(self._run_root(run_id) / "manifest.json")
        )

    def latest(self) -> RunManifest | None:
        manifests = sorted(
            self.runs_root.glob("EVAL-*/manifest.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return RunManifest.model_validate(read_json(manifests[0])) if manifests else None


class BackgroundEvaluationService:
    def __init__(self, runner: EvaluationRunner) -> None:
        self.runner = runner
        self._guard = threading.Lock()
        self._active_run_id: str | None = None

    @property
    def active_run_id(self) -> str | None:
        with self._guard:
            return self._active_run_id

    def start(self, **kwargs: object) -> RunManifest:
        with self._guard:
            if self._active_run_id:
                active = self.runner.load(self._active_run_id)
                if active.status == "RUNNING":
                    raise RuntimeError(f"이미 실행 중인 평가가 있습니다: {self._active_run_id}")
                self._active_run_id = None
            manifest = self.runner.create_manifest(**kwargs)
            self._active_run_id = manifest.run_id

        def worker() -> None:
            try:
                self.runner.execute(manifest)
            finally:
                with self._guard:
                    self._active_run_id = None

        threading.Thread(
            target=worker,
            name=f"eval-{manifest.run_id}",
            daemon=True,
        ).start()
        return manifest