from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .models import CheckStatus, GateDecision, Severity


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULE_CATALOG = PROJECT_ROOT / "rules" / "project1_agent_rules.yaml"
DEFAULT_BENCHMARK = PROJECT_ROOT / "benchmarks" / "development" / "agent_behavior_cases.yaml"
DEFAULT_SELF_TEST_TRIALS = PROJECT_ROOT / "fixtures" / "evaluator_self_test" / "recorded_trials.yaml"


class BehaviorRuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    status: CheckStatus
    severity: Severity
    reason: str = ""


class RecordedTrial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trial_id: str
    case_id: str
    agent_id: str
    core_outcome: dict[str, Any]
    rule_results: list[BehaviorRuleResult]
    unsupported_claims: list[str] = Field(default_factory=list)
    critical_violations: list[str] = Field(default_factory=list)


class CaseBehaviorSummary(BaseModel):
    case_id: str
    agent_id: str
    trial_count: int
    rule_compliance_rate: float
    stability_rate: float
    hallucination_trial_rate: float
    unsupported_claim_count: int
    critical_violation_count: int
    abstained_check_count: int
    dominant_outcome_count: int
    decision: GateDecision


class AgentBehaviorSummary(BaseModel):
    agent_id: str
    case_count: int
    trial_count: int
    rule_compliance_rate: float
    mean_stability_rate: float
    hallucination_trial_rate: float
    unsupported_claim_count: int
    critical_violation_count: int
    decision: GateDecision


class BehaviorEvaluationSummary(BaseModel):
    schema_version: str = "agent-behavior-summary/v1"
    evaluation_mode: str
    agent_executed: bool
    rule_catalog_version: str
    benchmark_version: str
    rule_count: int
    benchmark_case_count: int
    evaluated_case_count: int
    total_trials: int
    rule_compliance_rate: float
    mean_stability_rate: float
    hallucination_trial_rate: float
    unsupported_claim_count: int
    critical_violation_count: int
    cases: list[CaseBehaviorSummary]
    agents: list[AgentBehaviorSummary]
    decision: GateDecision
    truth_note: str
    evaluation_issues: list[str] = Field(default_factory=list)
    performance_claim_eligible: bool = False


def _read_yaml(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"YAML 최상위 값은 객체여야 합니다: {path}")
    return payload


def load_rule_catalog(path: Path = DEFAULT_RULE_CATALOG) -> dict[str, Any]:
    payload = _read_yaml(path)
    rules = payload.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("Rule Catalog에 rules가 없습니다.")
    rule_ids = [rule.get("rule_id") for rule in rules if isinstance(rule, dict)]
    if len(rule_ids) != len(set(rule_ids)) or any(not rule_id for rule_id in rule_ids):
        raise ValueError("Rule ID는 비어 있지 않고 고유해야 합니다.")
    return payload


def load_benchmark(path: Path = DEFAULT_BENCHMARK) -> dict[str, Any]:
    payload = _read_yaml(path)
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark에 cases가 없습니다.")
    case_ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    if len(case_ids) != len(set(case_ids)) or any(not case_id for case_id in case_ids):
        raise ValueError("Case ID는 비어 있지 않고 고유해야 합니다.")
    return payload


def validate_catalog_links(catalog: dict[str, Any], benchmark: dict[str, Any]) -> list[str]:
    rule_ids = {rule["rule_id"] for rule in catalog["rules"]}
    errors: list[str] = []
    for case in benchmark["cases"]:
        for rule_id in case.get("required_rule_ids", []):
            if rule_id not in rule_ids:
                errors.append(f"{case['case_id']}: unknown rule {rule_id}")
    return errors


def load_recorded_trials(
    path: Path = DEFAULT_SELF_TEST_TRIALS,
) -> tuple[dict[str, Any], list[RecordedTrial]]:
    payload = _read_yaml(path)
    trials = [RecordedTrial.model_validate(item) for item in payload.get("trials", [])]
    if not trials:
        raise ValueError("Recorded Trial이 없습니다.")
    return payload, trials


def _signature(core_outcome: dict[str, Any]) -> str:
    return json.dumps(core_outcome, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decision(
    *,
    compliance: float,
    stability: float,
    hallucination_trials: int,
    critical_violations: int,
    abstained: int,
) -> GateDecision:
    if critical_violations or hallucination_trials or compliance < 95.0:
        return GateDecision.HOLD
    if abstained or stability < 90.0:
        return GateDecision.HUMAN_REVIEW
    return GateDecision.PASS


def evaluate_recorded_trials(
    trials: list[RecordedTrial],
    *,
    evaluation_mode: str,
    agent_executed: bool,
    catalog: dict[str, Any] | None = None,
    benchmark: dict[str, Any] | None = None,
) -> BehaviorEvaluationSummary:
    catalog = catalog or load_rule_catalog()
    benchmark = benchmark or load_benchmark()
    link_errors = validate_catalog_links(catalog, benchmark)
    if link_errors:
        raise ValueError("; ".join(link_errors))

    if not trials:
        raise ValueError("평가할 Trial이 없습니다.")
    trial_ids = [t.trial_id for t in trials]
    if any(not i.strip() for i in trial_ids) or len(set(trial_ids)) != len(trial_ids):
        raise ValueError("Trial ID가 비어 있거나 중복되었습니다.")
    rules_by_id = {r["rule_id"]: r for r in catalog["rules"]}
    cases_by_id = {c["case_id"]: c for c in benchmark["cases"]}
    issues: list[str] = []

    catalog_rule_ids = {rule["rule_id"] for rule in catalog["rules"]}
    benchmark_case_ids = {case["case_id"] for case in benchmark["cases"]}
    for trial in trials:
        if trial.case_id not in benchmark_case_ids:
            raise ValueError(f"Benchmark에 없는 Case입니다: {trial.case_id}")
        unknown = {result.rule_id for result in trial.rule_results} - catalog_rule_ids
        if unknown:
            raise ValueError(f"Rule Catalog에 없는 Rule입니다: {sorted(unknown)}")
        case = cases_by_id[trial.case_id]
        if trial.agent_id != case["target"]:
            raise ValueError("Benchmark의 담당 Agent와 Trial이 다릅니다.")
        ids = [r.rule_id for r in trial.rule_results]
        if len(ids) != len(set(ids)):
            raise ValueError("Trial 내 검사 ID가 중복되었습니다.")
        if set(trial.critical_violations) - catalog_rule_ids:
            raise ValueError("알 수 없는 Critical Rule입니다.")
        for result in trial.rule_results:
            if result.severity.value != rules_by_id[result.rule_id]["severity"]:
                raise ValueError("검사 중요도가 고정 Rule Catalog와 다릅니다.")
        missing = set(case["required_rule_ids"]) - set(ids)
        if missing:
            issues.append(f"{trial.trial_id}: 필수 검사 누락 {sorted(missing)}")
        incomplete = [r.rule_id for r in trial.rule_results if r.status in
                      {CheckStatus.ERROR, CheckStatus.ABSTAIN} or
                      (r.status == CheckStatus.NOT_APPLICABLE and r.rule_id in case["required_rule_ids"])]
        if incomplete:
            issues.append(f"{trial.trial_id}: 검사 미완료 {incomplete}")
        # Legacy expected_core is not a semantic grader. Missing/unmatched keys must
        # never be silently treated as correct or as an independently scored trial.
        for key, expected in case.get("expected_core", {}).items():
            if key not in trial.core_outcome or _signature({key: trial.core_outcome[key]}) != _signature({key: expected}):
                issues.append(f"{trial.trial_id}: 정답 조건 미확인/불일치 {key}")

    grouped: dict[str, list[RecordedTrial]] = defaultdict(list)
    for trial in trials:
        grouped[trial.case_id].append(trial)

    def compliance_rate(items):
        passed = total = 0
        for trial in items:
            required = set(cases_by_id[trial.case_id]["required_rule_ids"])
            actual = {r.rule_id: r for r in trial.rule_results}
            counted = required | {r.rule_id for r in trial.rule_results if r.status != CheckStatus.NOT_APPLICABLE}
            total += len(counted)
            passed += sum(k in actual and actual[k].status == CheckStatus.PASS for k in counted)
        return round(passed / total * 100, 1) if total else 0.0

    case_summaries: list[CaseBehaviorSummary] = []
    for case_id, case_trials in sorted(grouped.items()):
        if len(case_trials) != cases_by_id[case_id]["trials"]:
            issues.append(f"{case_id}: 예정 {cases_by_id[case_id]['trials']}회 / 기록 {len(case_trials)}회")
        agent_ids = {trial.agent_id for trial in case_trials}
        if len(agent_ids) != 1:
            raise ValueError(f"한 Case에 여러 Agent가 섞였습니다: {case_id}")
        rule_results = [result for trial in case_trials for result in trial.rule_results]
        applicable = [
            result
            for result in rule_results
            if result.status in {CheckStatus.PASS, CheckStatus.FAIL}
        ]
        passed = sum(result.status == CheckStatus.PASS for result in applicable)
        compliance = compliance_rate(case_trials)
        signatures = Counter(_signature(trial.core_outcome) for trial in case_trials)
        dominant = max(signatures.values())
        stability = round(dominant / len(case_trials) * 100, 1)
        hallucination_trials = sum(bool(trial.unsupported_claims) for trial in case_trials)
        hallucination_rate = round(hallucination_trials / len(case_trials) * 100, 1)
        critical_keys = {
            f"{trial.trial_id}:{rule_id}"
            for trial in case_trials
            for rule_id in trial.critical_violations
        }
        critical_keys.update(
            f"{trial.trial_id}:{result.rule_id}"
            for trial in case_trials
            for result in trial.rule_results
            if result.status == CheckStatus.FAIL and result.severity == Severity.CRITICAL
        )
        abstained = sum(result.status == CheckStatus.ABSTAIN for result in rule_results)
        case_summaries.append(
            CaseBehaviorSummary(
                case_id=case_id,
                agent_id=next(iter(agent_ids)),
                trial_count=len(case_trials),
                rule_compliance_rate=compliance,
                stability_rate=stability,
                hallucination_trial_rate=hallucination_rate,
                unsupported_claim_count=sum(len(trial.unsupported_claims) for trial in case_trials),
                critical_violation_count=len(critical_keys),
                abstained_check_count=abstained,
                dominant_outcome_count=dominant,
                decision=_decision(
                    compliance=compliance,
                    stability=stability,
                    hallucination_trials=hallucination_trials,
                    critical_violations=len(critical_keys),
                    abstained=abstained + int(any(i.startswith(case_id + ":") or
                        any(i.startswith(t.trial_id + ":") for t in case_trials) for i in issues)),
                ),
            )
        )

    agent_groups: dict[str, list[CaseBehaviorSummary]] = defaultdict(list)
    for summary in case_summaries:
        agent_groups[summary.agent_id].append(summary)

    agent_summaries: list[AgentBehaviorSummary] = []
    for agent_id, cases in sorted(agent_groups.items()):
        agent_trials = [trial for trial in trials if trial.agent_id == agent_id]
        agent_rule_results = [result for trial in agent_trials for result in trial.rule_results]
        applicable = [
            result
            for result in agent_rule_results
            if result.status in {CheckStatus.PASS, CheckStatus.FAIL}
        ]
        passed = sum(result.status == CheckStatus.PASS for result in applicable)
        compliance = compliance_rate(agent_trials)
        hallucination_trials = sum(bool(trial.unsupported_claims) for trial in agent_trials)
        decisions = {case.decision for case in cases}
        decision = (
            GateDecision.HOLD
            if GateDecision.HOLD in decisions
            else GateDecision.HUMAN_REVIEW
            if GateDecision.HUMAN_REVIEW in decisions
            else GateDecision.PASS
        )
        agent_summaries.append(
            AgentBehaviorSummary(
                agent_id=agent_id,
                case_count=len(cases),
                trial_count=len(agent_trials),
                rule_compliance_rate=compliance,
                mean_stability_rate=round(
                    sum(case.stability_rate for case in cases) / len(cases), 1
                ),
                hallucination_trial_rate=round(
                    hallucination_trials / len(agent_trials) * 100, 1
                ),
                unsupported_claim_count=sum(
                    len(trial.unsupported_claims) for trial in agent_trials
                ),
                critical_violation_count=sum(
                    case.critical_violation_count for case in cases
                ),
                decision=decision,
            )
        )

    all_rule_results = [result for trial in trials for result in trial.rule_results]
    applicable = [
        result
        for result in all_rule_results
        if result.status in {CheckStatus.PASS, CheckStatus.FAIL}
    ]
    passed = sum(result.status == CheckStatus.PASS for result in applicable)
    decisions = {case.decision for case in case_summaries}
    overall_decision = (
        GateDecision.HOLD
        if GateDecision.HOLD in decisions
        else GateDecision.HUMAN_REVIEW
        if GateDecision.HUMAN_REVIEW in decisions
        else GateDecision.PASS
    )
    hallucination_trials = sum(bool(trial.unsupported_claims) for trial in trials)
    return BehaviorEvaluationSummary(
        evaluation_mode=evaluation_mode,
        agent_executed=agent_executed,
        rule_catalog_version=catalog["catalog_version"],
        benchmark_version=benchmark["dataset_version"],
        rule_count=len(catalog["rules"]),
        benchmark_case_count=len(benchmark["cases"]),
        evaluated_case_count=len(case_summaries),
        total_trials=len(trials),
        rule_compliance_rate=compliance_rate(trials),
        mean_stability_rate=round(
            sum(case.stability_rate for case in case_summaries) / len(case_summaries), 1
        ),
        hallucination_trial_rate=round(
            hallucination_trials / len(trials) * 100, 1
        ),
        unsupported_claim_count=sum(len(trial.unsupported_claims) for trial in trials),
        critical_violation_count=sum(
            case.critical_violation_count for case in case_summaries
        ),
        cases=case_summaries,
        agents=agent_summaries,
        decision=overall_decision,
        truth_note=(
            "제공된 실행 기록의 집계이며 호출 증거·의미 정답 검증이 완료된 Agent 성능 지표가 아닙니다."
            if agent_executed
            else "합성 Recorded Trial로 집계기만 검증한 Evaluator Self-test이며 Agent 성능 지표가 아닙니다."
        ),
        evaluation_issues=issues,
    )


def run_evaluator_self_test() -> BehaviorEvaluationSummary:
    payload, trials = load_recorded_trials()
    return evaluate_recorded_trials(
        trials,
        evaluation_mode="evaluator_self_test",
        agent_executed=bool(payload.get("agent_executed", False)),
    )


def evaluation_design() -> dict[str, Any]:
    catalog = load_rule_catalog()
    benchmark = load_benchmark()
    errors = validate_catalog_links(catalog, benchmark)
    if errors:
        raise ValueError("; ".join(errors))
    by_target = Counter(rule["target"] for rule in catalog["rules"])
    return {
        "primary_objective": "프로젝트 1 Agent의 규칙 준수·반복 안정성·근거 없는 생성을 평가",
        "rule_catalog_version": catalog["catalog_version"],
        "benchmark_version": benchmark["dataset_version"],
        "rule_count": len(catalog["rules"]),
        "case_count": len(benchmark["cases"]),
        "rules_by_target": dict(sorted(by_target.items())),
        "metrics": [
            "rule_compliance_rate",
            "stability_rate",
            "hallucination_trial_rate",
            "critical_violation_count",
        ],
        "live_agent_evaluation_ready": False,
        "current_runner_role": "evaluator_self_test_and_artifact_audit",
        "next_required_adapter": "전체 A1~4 실행·수집·역할별 평가·Checkpoint/연결 평가",
        "primary_evaluation_mode": "full_pipeline_repeat",
        "planned_trials_per_scenario": 5,
    }
