from __future__ import annotations

from eval_harness.cli import evaluation_exit_code, parser


def test_dashboard_has_no_host_override() -> None:
    args = parser().parse_args(["dashboard", "--no-browser"])
    assert not hasattr(args, "host")
    assert args.port == 8765


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