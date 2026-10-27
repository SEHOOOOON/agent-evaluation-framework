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


def _read(name):
    return (PAGE / name).read_text(encoding="utf-8")


def test_homepage_is_readable_without_disclosures_or_javascript():
    # 2026-10-25 사용자 결정: 소개는 접기·스크립트 없이 왜 필요한가 → 방법 → 사례 2개 → 결과 → 한계 순서로 읽힌다.
    text = _read("index.html")
    page = Links(text)
    assert "script" not in page.tags and "details" not in page.tags and " hidden" not in text
    order = ("why", "method", "case-range", "case-restore", "judge", "results", "scope")
    positions = [text.index('id="' + name + '"') for name in order]
    assert positions == sorted(positions)
    assert len(page.ids) == len(set(page.ids))
    # 실행 날짜는 소개·보고서 본문에 쓰지 않는다(2026-10-25 사용자 결정).
    for name in ("index.html", "report.html", "scores.html"):
        assert not re.search(r"2026[.\-]\d|\d{1,2}월 \d{1,2}일", _read(name)), name


def test_new_report_is_static_and_points_to_scores_and_detail():
    report = _read("report.html")
    page = Links(report)
    assert "script" not in page.tags
    positions = [report.index('id="' + name + '"') for name in ("latest", "repeat", "human", "improvements")]
    assert positions == sorted(positions)
    assert '<details class="story-fold" id="models">' in report
    assert 'href="scores.html"' in report
    assert "story-cta" not in report


def test_scores_page_lists_all_26_criteria_for_five_requests():
    scores = _read("scores.html")
    rows = re.findall(r'<tr><th scope="row"><span class="cid">([^<]+)</span>', scores)
    assert len(rows) == len(set(rows)) == 26
    header = re.search(r"<thead>(.*?)</thead>", scores, re.S).group(1)
    assert header.count('class="req"') == 5
    # 온도 요청의 두 감점(정답 시험 4점, 복원 3점)은 점수표와 설명에 같이 남긴다.
    assert re.search(r'TC-R05</span>[^<]*</th><td class="who">프로그램</td>(<td[^>]*>[^<]*</td>){4}<td class="s4">4</td>', scores)
    assert re.search(r'TC-R07</span>[^<]*</th><td class="who">AI</td>(<td[^>]*>[^<]*</td>){4}<td class="s3">3</td>', scores)


def test_intro_and_report_scores_agree():
    home = _read("index.html")
    report = _read("report.html")
    # 2026-10-27: 잠금은 분석 단계까지만 평가해 평균 대신 충족 수(4/4)로 표시한다.
    intro = re.findall(r'<td><b(?: class="scenario-warning")?>([0-9./]+)</b>', home)
    summary = re.findall(r'<div(?: class="warn")?><span>[^<]+</span><b>([0-9./]+)</b>', report)
    assert intro == summary == ["4/4", "5.0", "5.0", "5.0", "4.86"]


def test_intro_keeps_core_cases_and_limits():
    text = _read("index.html")
    for phrase in ("필수 시험 5개 중 3개", "8번 모두 5개", "4번 모두 빠짐", "3번 모두 포함",
                   "12개를 다시 비교했고, 모두 같았습니다", "점수만으로 합격을 정하지 않습니다",
                   "이 누락은 사람이 실행 결과를 검토하다 먼저 찾았고"):
        assert phrase in text, phrase
    assert text.count('class="by"') == 4


def test_portfolio_links_resolve_to_files_and_anchors():
    unchecked = []
    for source in ("index.html", "report.html", "scores.html"):
        page = Links(_read(source))
        for link in page.links:
            if link.startswith(("http://", "https://")):
                continue
            if link.startswith("#"):
                assert link[1:] in page.ids, (source, link)
                continue
            name, _, anchor = link.partition("#")
            target = Path(os.path.normpath(PAGE / name))
            # A sibling repository that is not checked out is reported, not failed.
            if ROOT not in target.parents and not (ROOT.parent / target.relative_to(ROOT.parent).parts[0]).is_dir():
                unchecked.append(link)
                continue
            assert target.is_file(), (source, link)
            if anchor:
                assert anchor in Links(target.read_text(encoding="utf-8")).ids, (source, link)
    if unchecked:
        pytest.skip(f"형제 저장소 없음: 링크 {len(unchecked)}개 미확인 {sorted(set(unchecked))}")


def test_report_detail_exposes_original_details_and_keeps_legacy_anchor_routes():
    # 2026-10-25: 이전 상세 보고서는 report-detail.html로 보존한다(새 report.html은 정적 요약).
    report = _read("report-detail.html")
    script = _read("report-view.js")
    legacy = _read("story.js")
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


def test_report_detail_dates_remain_record_metadata():
    text = _read("report-detail.html")
    assert 'id="record-metadata"' in text
    for day in ('04', '05', '09'):
        assert f'datetime="2026-10-{day}"' in text
    assert '<p class="reason-method">2026.10.09' not in text
    assert 'href="#record-metadata"' in text


def test_full_power_record_has_one_disclosure_level():
    script = _read("report-view.js")
    assert "'power-result-details'" in script
    assert "detail.classList.contains('latest-role')" not in script
