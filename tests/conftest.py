from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def known_bad_root(project_root: Path) -> Path:
    return project_root / "fixtures" / "known_bad" / "project1"


@pytest.fixture
def known_good_root(project_root: Path) -> Path:
    return project_root / "fixtures" / "known_good" / "project1"


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "evaluation-runs"

