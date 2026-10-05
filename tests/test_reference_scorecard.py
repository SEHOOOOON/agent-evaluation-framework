"""Draft reference labels are review artifacts, not a live model score or oracle."""
import json
from pathlib import Path

import pytest

from eval_harness.detailed_rubric import CRITERIA, VERSION, SCORE_VERSION, PYTHON_CRITERIA, score_is_valid

PATH = Path(__file__).resolve().parents[1] / 'benchmarks/v2/reference_scorecard.draft.json'
DRAFT = json.loads(PATH.read_text(encoding='utf-8'))


def test_reference_is_unapproved_and_not_repeated_model_evidence():
    assert DRAFT['oracle_status'] == 'DRAFT'
    assert DRAFT['human_reviewed'] is False
    assert DRAFT['mode'] == 'SAVED_EVIDENCE_REFERENCE_DRAFT'
    assert DRAFT['paid_api_calls'] == DRAFT['new_p1_runs'] == 0
    assert DRAFT['fact_evaluations_per_case'] == 1
    assert DRAFT['model_repeat_variability_measured'] is False
    # This is the preserved pre-change review, not a silently re-rated 2.3 oracle.
    assert DRAFT['rubric_version'] == 'role-rubric-2.2'
    assert DRAFT['scoring_profile'] == SCORE_VERSION


@pytest.mark.parametrize('case', DRAFT['cases'], ids=lambda case: case['case_id'])
def test_reference_has_all_criteria_and_valid_provisional_scores(case):
    rows = case['rows']
    assert len(rows) == len(CRITERIA) == 26
    assert {row['criterion_id'] for row in rows} == {item['criterion_id'] for item in CRITERIA}
    for row in rows:
        assert score_is_valid(row['score'], row['verdict'], SCORE_VERSION)
        assert row['reason'].strip() and row['evidence']
        # Preserve the historical 2.2 ownership, not today's A3-03 migration.
        assert row['basis'] == ('PYTHON_FACT' if row['criterion_id'] in {'A4-01', 'A4-03', 'E04'} else 'CODEX_REVIEW')
        for evidence in row['evidence']:
            assert not Path(evidence).is_absolute()
            assert '..' not in Path(evidence).parts and ':' not in evidence
    by_id = {row['criterion_id']: row for row in rows}
    assert by_id['E04']['score'] is None and by_id['E04']['verdict'] == 'REVIEW'


def test_product_failure_is_not_a_blanket_agent_failure():
    failed = next(case for case in DRAFT['cases'] if case['product_outcome'] == 'ASSERTION_FAILED')
    rows = {row['criterion_id']: row for row in failed['rows']}
    assert rows['A4-02']['score'] == 5 and rows['A4-02']['verdict'] == 'PASS'
    assert rows['TC-R07']['verdict'] == rows['A3-04']['verdict'] == 'FAIL'
    assert rows['TC-R06']['score'] is None  # Historical proposal evidence absent.
    assert len({case['run_id'] for case in DRAFT['cases']}) == len(DRAFT['cases'])
