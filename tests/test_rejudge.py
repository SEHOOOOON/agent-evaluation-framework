"""rejudge-flow: re-judge a stored trial from evidence.zip (test doubles only)."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from eval_harness import rejudge
from eval_harness.cli import main
from eval_harness.v2_recorded import read

from test_full_flow import execute, flow_setup, worker_for  # noqa: F401  (fixture)


class FakeClient:
    """Fails the call so that no parsing contract is assumed; still counts as one attempt."""
    calls = 0

    class responses:
        @staticmethod
        def parse(**_):
            FakeClient.calls += 1
            raise TimeoutError("simulated")


@pytest.fixture
def stored(flow_setup):
    root, result = execute(flow_setup, worker_for())
    return root


def tree(path: Path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_preview_rebuilds_packets_without_calls_or_writes(stored, tmp_path):
    before = tree(stored)
    info = rejudge.make_preview(stored, 1, ["A4", "A3"])
    assert list(info["roles"]) == ["A3", "A4"]
    assert info["executable"] and info["maximum_api_attempts"] == 2
    assert all(r["status"] == "READY" and r["payload_bytes"] > 0 for r in info["roles"].values())
    # Same stored packets as the original run for the same code.
    original = read(stored / "trial-1" / "A3" / "preview.json")["approval_sha256"]
    assert info["roles"]["A3"]["approval_sha256"] == original
    assert tree(stored) == before
    assert rejudge.make_preview(stored, 1, ["A3", "A4"])["approval_sha256"] == info["approval_sha256"]
    assert rejudge.make_preview(stored, 1, ["A3"])["approval_sha256"] != info["approval_sha256"]


def test_tampered_archive_is_refused(stored):
    archive = stored / "trial-1" / "evidence.zip"
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("extra.txt", "x")
    with pytest.raises(ValueError, match="Evidence archive"):
        rejudge.make_preview(stored, 1, ["A3"])


def test_execute_requires_exact_approval_and_keeps_flow_untouched(stored, tmp_path):
    before = tree(stored)
    info = rejudge.make_preview(stored, 1, ["A3", "A4"])
    with pytest.raises(ValueError, match="approval"):
        rejudge.execute(stored, 1, ["A3", "A4"], "0" * 64, tmp_path / "out", client_factory=lambda: FakeClient())
    FakeClient.calls = 0
    out = rejudge.execute(stored, 1, ["A3", "A4"], info["approval_sha256"], tmp_path / "out",
                          client_factory=lambda: FakeClient())
    summary = read(out / "summary.json")
    # The first judge error stops the batch: no second call.
    assert FakeClient.calls == 1 and summary["status"] == "ERROR"
    assert list(summary["results"]) == ["A3"] and summary["original_scores_unchanged"]
    assert tree(stored) == before
    with pytest.raises(ValueError, match="inside"):
        rejudge.execute(stored, 1, ["A3"], rejudge.make_preview(stored, 1, ["A3"])["approval_sha256"], stored / "x",
                        client_factory=lambda: FakeClient())


def test_unknown_role_packet_is_reported(stored, monkeypatch):
    real = rejudge.collect_packets
    monkeypatch.setattr(rejudge, "collect_packets", lambda *a: {k: v for k, v in real(*a).items() if k != "A4"})
    info = rejudge.make_preview(stored, 1, ["A3", "A4"])
    assert info["roles"]["A4"]["status"] == "NOT_AVAILABLE" and not info["executable"]


def test_oversized_payload_is_blocked_not_trimmed(stored, monkeypatch):
    monkeypatch.setattr("eval_harness.openai_judge.MAX_JUDGE_PAYLOAD_BYTES", 10)
    info = rejudge.make_preview(stored, 1, ["A3"])
    assert info["roles"]["A3"]["status"] == "BLOCKED" and "too large" in info["roles"]["A3"]["reason"]
    assert not info["executable"]


def test_cli_preview_and_missing_approval(stored, capsys):
    assert main(["rejudge-flow", "--flow", str(stored), "--roles", "A3"]) == 0
    assert json.loads(capsys.readouterr().out)["roles"]["A3"]["status"] == "READY"
    assert main(["rejudge-flow", "--flow", str(stored), "--roles", "A3", "--execute"]) == 4
