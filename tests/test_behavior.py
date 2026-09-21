from __future__ import annotations

from eval_harness.behavior import (
    evaluation_design,
    load_benchmark,
    load_rule_catalog,
    run_evaluator_self_test,
    validate_catalog_links,
)
from eval_harness.models import GateDecision


def test_rule_catalog_and_benchmark_links_are_valid() -> None:
    catalog = load_rule_catalog()
    benchmark = load_benchmark()

    assert catalog["catalog_version"] == "RULES-v0.3"
    assert benchmark["dataset_version"] == "BENCH-v0.4-development"
    assert len(catalog["rules"]) >= 30
    assert len(benchmark["cases"]) == 9
    assert validate_catalog_links(catalog, benchmark) == []


def test_evaluator_self_test_detects_seeded_quality_failures() -> None:
    summary = run_evaluator_self_test()

    assert summary.agent_executed is False
    assert summary.evaluation_mode == "evaluator_self_test"
    assert summary.evaluated_case_count == 2
    assert summary.total_trials == 6
    assert summary.rule_compliance_rate < 95.0
    assert summary.mean_stability_rate < 90.0
    assert summary.hallucination_trial_rate > 0.0
    assert summary.critical_violation_count >= 2
    assert summary.decision == GateDecision.HOLD
    assert "Agent 성능 지표가 아닙니다" in summary.truth_note


def test_evaluation_design_discloses_live_adapter_limit() -> None:
    design = evaluation_design()

    assert design["live_agent_evaluation_ready"] is False
    assert design["rule_count"] >= 30
    assert design["case_count"] == 9
    assert set(design["metrics"]) == {
        "rule_compliance_rate",
        "stability_rate",
        "hallucination_trial_rate",
        "critical_violation_count",
    }
