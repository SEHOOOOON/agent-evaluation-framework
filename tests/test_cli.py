from __future__ import annotations

from eval_harness.cli import evaluation_exit_code, parser


def test_dashboard_has_no_host_override() -> None:
    args = parser().parse_args(["dashboard", "--no-browser"])
    assert not hasattr(args, "host")
    assert args.port == 8766  # Project 1 UI keeps 8765.


def test_v1_fixture_root_is_a_sibling_checkout_not_a_user_path() -> None:
    from pathlib import Path
    from eval_harness.runner import DEFAULT_PROJECT1_ROOT
    repo = Path(__file__).resolve().parents[1]
    assert DEFAULT_PROJECT1_ROOT == repo.parent / "portfolio_export"
    assert "Users" not in DEFAULT_PROJECT1_ROOT.relative_to(repo.parent).parts


def test_run_cli_is_fixture_only() -> None:
    args = parser().parse_args(["run"])
    assert args.mode == "fixture"
    assert args.suite == "project1-audit"
    assert args.trials == 1



def test_evaluation_exit_codes_are_ci_readable() -> None:
    assert evaluation_exit_code("COMPLETED", "PASS") == 0
    assert evaluation_exit_code("COMPLETED", "HOLD") == 2
    assert evaluation_exit_code("COMPLETED", "HUMAN_REVIEW") == 3
    assert evaluation_exit_code("ERROR", None) == 4