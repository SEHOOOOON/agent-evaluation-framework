from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ABSTAIN = "ABSTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


class StageStatus(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"


class GateDecision(str, Enum):
    PASS = "PASS"
    HOLD = "HOLD"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class CheckResult(BaseModel):
    criterion_id: str
    title: str
    status: CheckStatus
    severity: Severity
    reason: str
    expected: Any = None
    actual: Any = None
    evidence_refs: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    finding_id: str
    criterion_id: str
    title: str
    severity: Severity
    reason: str
    recommended_action: str
    evidence_refs: list[str] = Field(default_factory=list)


class StageResult(BaseModel):
    stage_id: str
    step: int
    agent_name: str
    agent_mode: str
    component_type: str
    sut_executed: bool = False
    checkpoint_id: str
    checkpoint_status: str
    status: StageStatus
    mode_note: str
    input_refs: list[str] = Field(default_factory=list)
    raw_output_ref: str | None = None
    normalized_output_ref: str | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    started_at: str
    completed_at: str | None = None


class RunSummary(BaseModel):
    checks_passed: int = 0
    checks_failed: int = 0
    checks_abstained: int = 0
    checks_not_applicable: int = 0
    checks_error: int = 0
    critical_findings: int = 0
    high_findings: int = 0
    total_findings: int = 0


class RunManifest(BaseModel):
    schema_version: str = "1.0"
    run_id: str
    status: str
    suite: str
    execution_mode: str
    sut_executed: bool = False
    stability_evaluated: bool = False
    trial_count: int
    target_version: str
    source_name: str
    source_hash_before: str | None = None
    source_hash_after: str | None = None
    source_unchanged: bool | None = None
    started_at: str
    completed_at: str | None = None
    stages: list[StageResult] = Field(default_factory=list)
    summary: RunSummary = Field(default_factory=RunSummary)
    release_decision: GateDecision | None = None
    report_available: bool = False
    error: str | None = None


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str = "project1-audit"
    execution_mode: str = "fixture"
    target_version: str = "project1-current"
    trials: int = Field(default=1, ge=1, le=1)

