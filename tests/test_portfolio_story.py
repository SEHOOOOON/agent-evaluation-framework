"""Narrative/report presentation contracts; not model evaluations."""
from html.parser import HTMLParser
from pathlib import Path
import re

PAGE = Path(__file__).resolve().parents[1] / "portfolio"


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
    assert "새 AI 반복 실행은 아직 검증하지 않았습니다" in text
    assert "사람의 최종 검토 전" in text
    # Version-level provenance belongs in the evidence page, not the introduction.
    report = (PAGE / "report.html").read_text(encoding="utf-8")
    assert "2.2에서 2.3" in report
    assert "AI가 100번 새로 판단한 결과가 아닙니다" in text


def test_all_six_summary_rows_are_identical_to_preserved_report():
    home = (PAGE / "index.html").read_text(encoding="utf-8")
    report = (PAGE / "report.html").read_text(encoding="utf-8")
    pattern = r'<tr data-summary="([^"]+)">(.*?)</tr>'
    expected = dict(re.findall(pattern, report, re.S))
    actual = dict(re.findall(pattern, home, re.S))
    assert set(actual) == set(expected) == {"power", "normal", "product", "omission", "pause", "reuse"}
    for name, row in actual.items():
        assert row.replace('href="report.html#', 'href="#') == expected[name]


def test_homepage_links_resolve_to_files_and_report_anchors():
    page = Links((PAGE / "index.html").read_text(encoding="utf-8"))
    report = Links((PAGE / "report.html").read_text(encoding="utf-8"))
    for link in page.links:
        if link.startswith("#"):
            assert link[1:] in page.ids
        else:
            name, _, anchor = link.partition("#")
            assert (PAGE / name).is_file(), link
            if anchor:
                assert name == "report.html" and anchor in report.ids


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
    assert "가상 사본 · 제품 실행 아님" in text
    assert "2점·5점·0점" in text
    assert "평가 기준도 달라졌습니다" in text
    assert "연결 기준의 최종 평가는 아직 남아" in text
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
    assert "수동 검토 시간의 감소는 아직 측정하지 않았습니다" in text
    assert "최초 점수와 판단 이유는 상세 기록에 보존" in text
    assert "improvement-line" not in text


def test_full_power_record_has_one_disclosure_level():
    script = (PAGE / "report-view.js").read_text(encoding="utf-8")
    assert "'power-result-details'" in script
    assert "detail.classList.contains('latest-role')" not in script
