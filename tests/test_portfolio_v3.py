"""Portfolio v3 (2026-10-16): four self-contained pages that link only inside the
repository, so the set keeps working wherever the folder is hosted."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

V3 = Path(__file__).resolve().parents[1] / "archive/portfolio-v3-20261016"
PAGES = ["index.html", "project1.html", "project2.html", "project2-report.html"]
REPO = V3.parents[1]


@pytest.mark.parametrize("name", PAGES)
def test_local_links_and_anchors_resolve(name):
    text = (V3 / name).read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', text))
    for href in re.findall(r'(?:href|src|poster)="([^"]+)"', text):
        if href.startswith(("http", "mailto:")):
            continue
        target, _, anchor = href.partition("#")
        if not target:
            assert anchor in ids, f"{name}: missing anchor #{anchor}"
            continue
        path = (V3 / target).resolve()
        assert path.exists(), f"{name}: broken link {href}"
        # Never depend on a sibling checkout (the old pages broke when hosted).
        assert path.is_relative_to(REPO), f"{name}: link leaves the repository: {href}"


def test_backups_of_the_four_original_pages_are_kept():
    backups = sorted(p.name for p in (V3 / "backup-20261016").glob("*.html"))
    assert len(backups) == 4 and [b[0] for b in backups] == ["1", "2", "3", "4"]
