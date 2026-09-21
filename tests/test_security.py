from __future__ import annotations

from pathlib import Path

import pytest

from eval_harness.security import redact_data, redact_text, safe_child
from eval_harness.storage import ArtifactStore, atomic_write_json


def test_redacts_sensitive_keys_and_inline_values() -> None:
    assert redact_data({"api_key": "secret-value", "safe": "ok"}) == {
        "api_key": "***REDACTED***",
        "safe": "ok",
    }
    text = redact_text("token=abc123 normal=value")
    assert "abc123" not in text
    assert "normal=value" in text


def test_safe_child_rejects_parent_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError):
        safe_child(root, root / ".." / "outside.txt")


def test_artifact_store_uses_allowlist(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.add_text("SAFE-ARTIFACT", "hello")
    target, media_type = ArtifactStore.resolve_artifact(tmp_path, "SAFE-ARTIFACT")
    assert target.read_text(encoding="utf-8") == "hello"
    assert media_type == "text/plain"
    with pytest.raises(FileNotFoundError):
        ArtifactStore.resolve_artifact(tmp_path, "../manifest")


def test_artifact_snapshot_is_redacted(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    store.add_text("REDACTED", "webhook_url=https://example.invalid/private")
    target, _ = ArtifactStore.resolve_artifact(tmp_path, "REDACTED")
    assert "example.invalid" not in target.read_text(encoding="utf-8")



def test_atomic_json_write_retries_transient_windows_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import eval_harness.storage as storage

    original_replace = storage.os.replace
    attempts = {"count": 0}

    def flaky_replace(source: Path, target: Path) -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("simulated Windows reader lock")
        original_replace(source, target)

    monkeypatch.setattr(storage.os, "replace", flaky_replace)
    target = tmp_path / "manifest.json"
    atomic_write_json(target, {"status": "COMPLETED"})

    assert attempts["count"] == 3
    assert target.read_text(encoding="utf-8").find("COMPLETED") >= 0
    assert not list(tmp_path.glob("*.tmp"))