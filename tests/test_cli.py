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

def test_stale_installed_copy_is_refused_inside_a_checkout(tmp_path, capsys) -> None:
    """2026-10-15: `python -m eval_harness` at the repo root loaded an installed
    full-flow-0.15 copy instead of src/ (0.16). A checkout with its own source
    must not silently run a different copy."""
    from pathlib import Path
    from eval_harness.cli import main, source_mismatch
    repo = Path(__file__).resolve().parents[1]
    assert source_mismatch(repo) is None          # running this checkout's src
    assert source_mismatch(tmp_path) is None      # not a checkout: nothing to compare
    other = tmp_path / "src" / "eval_harness"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("", encoding="utf-8")
    message = source_mismatch(tmp_path)
    assert message and "PYTHONPATH=src" in message and "pip install -e" in message
    import os
    old = Path.cwd()
    os.chdir(tmp_path)
    try:
        assert main(["run"]) == 4
    finally:
        os.chdir(old)
    assert "STALE_INSTALLED_COPY" in capsys.readouterr().out
