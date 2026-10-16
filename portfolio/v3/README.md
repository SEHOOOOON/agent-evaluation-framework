# 포트폴리오 v3 (2026-10-16 · 검토용 초안)

기존 4개 페이지를 다시 구성한 새 버전입니다. 기존 파일은 원래 위치에 그대로 있고, 사본을 `backup-20261016/`에 보관했습니다.

| 새 페이지 | 대체 대상(백업 파일) | 원본 위치 |
|---|---|---|
| `index.html` 메인 | `1-main_V1-index_night-review-1c38d66.html` | `qa-agent-pipeline` 브랜치 `codex/night-review-20261009`의 `index.html` (PC의 `portfolio_export/main.html`은 GitHub에 없어 백업하지 못함) |
| `project1.html` 프로젝트1 | `2-project1_V2-project_3691587.html` | `qa-agent-pipeline-v2/project.html` |
| `project2.html` 프로젝트2 | `3-project2-index_308826a.html` | `portfolio/index.html` |
| `project2-report.html` 평가 근거 | `4-project2-report_308826a.html` | `portfolio/report.html` |

## 바꾼 점

- 네 페이지가 이 폴더 안에서만 서로 연결됩니다(형제 폴더 경로 `../../portfolio_export/...` 제거). 영상·포스터는 `assets/`에 복사했습니다.
- 각 페이지 첫 화면에 핵심 수치 4개를 두고, 프로젝트1은 12,793px → 약 5,500px(데스크톱)로 줄였습니다.
- 최신 사실 반영: 실제 AI 전체 실행 2건(10-09 전원, 10-15 온도), 10-15 난방 시험 통과, 사람 대조로 찾은 필수 시험 누락 3개와 코드 대조 결과(필수 3/6, FAIL).
- AI 채점 점수는 "초안 기준 AI 채점"으로 표시하고, 점수 포화(20개 중 19개 5점)를 한계로 적었습니다.

## 주의

- **버전 고정 전 수치**입니다. 고정 후 5회 실행 결과가 나오면 숫자를 갱신해야 합니다.
- 이 저장소는 비공개라 그대로는 외부에서 열 수 없습니다. 공개할 위치(예: 공개 저장소의 GitHub Pages)는 사용자 결정 사항입니다. 프로젝트2 근거 페이지의 `../../improvements/`·`../evidence/` 링크는 이 저장소를 함께 공개할 때만 열립니다.
