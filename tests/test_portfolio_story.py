"""Narrative/report presentation contracts; not model evaluations."""
from html.parser import HTMLParser
from pathlib import Path
import os
import re

import pytest

PAGE = Path(__file__).resolve().parents[1] / "portfolio"
ROOT = PAGE.parent


class Links(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.links, self.tags = [], [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag in ("a", "link", "script"):
            self.links.append(attrs.get("href") or attrs.get("src"))


def test_homepage_is_readable_without_disclosures_or_javascript():
    text = (PAGE / "index.html").read_text(encoding="utf-8")
    page = Links(text)
    assert "details" not in page.tags and " hidden" not in text
    positions = [text.index('id="' + name + '"') for name in ("purpose", "method", "results", "improvement", "scope")]
    assert positions == sorted(positions)
    assert len(page.ids) == len(set(page.ids))
    assert "page.js" not in text and "story.js" in text
    # Version-level provenance belongs in the evidence page, not the introduction.
    report = (PAGE / "report.html").read_text(encoding="utf-8")
    assert "2.2에서 2.3" in report
    # 2026-10-16 사용자 결정: 소개 페이지에는 상태·미검증 단서를 흩어 두지 않는다.
    assert "시험하지 못" not in text and "아직 검증하지 않았습니다" not in text


def test_all_six_summary_rows_are_identical_to_preserved_report():
    home = (PAGE / "index.html").read_text(encoding="utf-8")
    report = (PAGE / "report.html").read_text(encoding="utf-8")
    pattern = r'<tr data-summary="([^"]+)">(.*?)</tr>'
    expected = dict(re.findall(pattern, report, re.S))
    actual = dict(re.findall(pattern, home, re.S))
    assert set(actual) == set(expected) == {"power", "normal", "product", "omission", "pause", "reuse"}
    # 2026-10-16: the introduction is a shortened summary; the evidence page keeps
    # the detailed scope notes. Cases, titles and scores must still agree.
    for name, row in actual.items():
        assert re.findall(r'<b[^>]*>([^<]+)</b>', row) == re.findall(r'<b[^>]*>([^<]+)</b>', expected[name])
        assert re.search(r'<th scope="row">([^<]+)', row).group(1) == re.search(r'<th scope="row">([^<]+)', expected[name]).group(1)


def test_homepage_links_resolve_to_files_and_report_anchors():
    page = Links((PAGE / "index.html").read_text(encoding="utf-8"))
    report = Links((PAGE / "report.html").read_text(encoding="utf-8"))
    unchecked = []
    for link in page.links:
        if link.startswith("#"):
            assert link[1:] in page.ids
        else:
            name, _, anchor = link.partition("#")
            target = Path(os.path.normpath(PAGE / name))
            # A sibling repository that is not checked out is reported, not failed.
            if ROOT not in target.parents and not (ROOT.parent / target.relative_to(ROOT.parent).parts[0]).is_dir():
                unchecked.append(link)
                continue
            assert (PAGE / name).is_file(), link
            if anchor == "":
                continue
            if name == "report.html":
                assert anchor in report.ids, link
            else:
                # Shared top bar links to the sibling main page (2026-10-16).
                assert f'id="{anchor}"' in (PAGE / name).read_text(encoding="utf-8"), link
    if unchecked:
        pytest.skip(f"형제 저장소 없음: 링크 {len(unchecked)}개 미확인 {sorted(set(unchecked))}")


def test_report_exposes_original_details_and_keeps_legacy_anchor_routes():
    report = (PAGE / "report.html").read_text(encoding="utf-8")
    script = (PAGE / "report-view.js").read_text(encoding="utf-8")
    legacy = (PAGE / "story.js").read_text(encoding="utf-8")
    assert 'class="report-page"' in report
    assert report.index('src="page.js"') < report.index('src="report-view.js"')
    assert "panel.hidden = false" in script
    assert "querySelectorAll('details')" in script
    assert "section.append(...detail.childNodes)" in script
    assert "foldIds.has(detail.id)" in script
    assert "node.tagName === 'DETAILS'" in script
    assert "beforeprint" in script and "afterprint" in script
    assert "report.html#" in legacy and "encodeURIComponent(id)" in legacy
    for source in (script, legacy):
        assert not re.search(r"fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon", source)


def test_intro_explains_roles_and_compares_observation_with_missing_checks():
    text = (PAGE / "index.html").read_text(encoding="utf-8")
    assert 'class="role-checks"' in text
    assert 'class="evidence-example"' in text
    assert "기대 16°C → 실제 15°C" in text
    assert "검사를 일부러 뺀 사본" in text
    assert "2점·5점·0점" in text
    assert "평가 기준도 달라졌습니다" in text
    assert "자료가 다음 단계에 제대로 전달됐는지도 함께 확인" in text
    assert not re.search(r'10\.0[459]', text)


def test_dates_remain_available_as_record_metadata_not_repeated_reasons():
    text = (PAGE / "report.html").read_text(encoding="utf-8")
    assert 'id="record-metadata"' in text
    for day in ('04', '05', '09'):
        assert f'datetime="2026-10-{day}"' in text
    assert '<p class="reason-method">2026.10.09' not in text
    assert 'href="#record-metadata"' in text


def test_portfolio_connects_work_motivation_to_evaluation_without_claiming_savings():
    text = (PAGE / "index.html").read_text(encoding="utf-8")
    assert "실무에서는 테스트의 PASS·FAIL뿐 아니라" in text
    assert "어느 단계에서 무엇을 지켰고 놓쳤는지" in text
    assert "평가 AI의 판단도 확인이 필요했습니다" in text
    assert "AI 평가가 사람의 판단과 얼마나 일치하는지" in text
    assert "최초 점수와 판단 이유는 상세 기록에 보존" in text
    assert "improvement-line" not in text


def test_full_power_record_has_one_disclosure_level():
    script = (PAGE / "report-view.js").read_text(encoding="utf-8")
    assert "'power-result-details'" in script
    assert "detail.classList.contains('latest-role')" not in script
