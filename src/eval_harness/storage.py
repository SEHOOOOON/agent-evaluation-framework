from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from .security import redact_data, redact_text, safe_child


ARTIFACT_ID = re.compile(r"^[A-Za-z0-9._-]+$")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(redact_data(data), ensure_ascii=False, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(20):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.01)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    # Windows may briefly deny opening a file while the writer replaces it.
    # Retry only access contention; malformed JSON and persistent denial stay errors.
    for attempt in range(10):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.01)


class ArtifactStore:
    def __init__(self, run_root: Path) -> None:
        self.run_root = run_root.resolve()
        self.artifact_root = safe_child(self.run_root, self.run_root / "artifacts")
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.run_root / "artifact-index.json"
        self._index: dict[str, dict[str, str]] = {}

    def _register(self, artifact_id: str, file_name: str, media_type: str) -> str:
        if not ARTIFACT_ID.fullmatch(artifact_id):
            raise ValueError(f"안전하지 않은 artifact id: {artifact_id}")
        self._index[artifact_id] = {"file_name": file_name, "media_type": media_type}
        atomic_write_json(self.index_path, self._index)
        return artifact_id

    def add_text(self, artifact_id: str, text: str, suffix: str = ".txt") -> str:
        file_name = f"{artifact_id}{suffix}"
        target = safe_child(self.artifact_root, self.artifact_root / file_name)
        target.write_text(redact_text(text), encoding="utf-8")
        media_type = "text/markdown" if suffix == ".md" else "text/plain"
        return self._register(artifact_id, file_name, media_type)

    def add_json(self, artifact_id: str, data: Any) -> str:
        file_name = f"{artifact_id}.json"
        target = safe_child(self.artifact_root, self.artifact_root / file_name)
        atomic_write_json(target, data)
        return self._register(artifact_id, file_name, "application/json")

    @classmethod
    def resolve_artifact(cls, run_root: Path, artifact_id: str) -> tuple[Path, str]:
        if not ARTIFACT_ID.fullmatch(artifact_id):
            raise FileNotFoundError(artifact_id)
        run_root = run_root.resolve()
        index = read_json(safe_child(run_root, run_root / "artifact-index.json"))
        record = index.get(artifact_id)
        if not record:
            raise FileNotFoundError(artifact_id)
        target = safe_child(run_root, run_root / "artifacts" / record["file_name"])
        if not target.is_file():
            raise FileNotFoundError(artifact_id)
        return target, record["media_type"]

