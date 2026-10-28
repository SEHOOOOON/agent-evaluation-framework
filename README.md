# Agent Evaluation Framework (프로젝트 2)

프로젝트 1([QA Agent Pipeline V2](https://github.com/SEHOOOOON/qa-agent-pipeline-v2))의 Agent 1~4가 **기준대로 일하는지, 같은 조건에서 일관되게 일하는지**를 평가하는 도구입니다. TC나 최종 보고서 하나만 보지 않고, 단계마다 결과와 단계 사이 연결을 함께 봅니다.

## 무엇을 평가하나

| 대상 | 평가 내용 |
|---|---|
| Agent 1 | 요구사항의 수치·변경/유지 조건·미정 범위·질문·근거 |
| Agent 2 | 기존 TC 재사용·고쳐 쓰기 판단, 빠진 시험이 없는지, 조작·기대결과·복원이 상세한지 |
| Agent 3 | TC → 자동화 코드 → 실제 시험의 연결, 확인 위치·시점·값, 복원·증거 |
| Agent 4 | 결과 집계·실패 분류, 근거 연결, 미정·미실행 보고 |
| 전체 연결 | 잘못된 결과를 막고 정상 결과를 과하게 막지 않는지, 단계 사이 누락·변조 |

제품 시험 실패와 Agent 평가 실패는 다릅니다. 제품의 불일치를 정확히 찾아 보고했다면 Agent는 제 역할을 한 것입니다.

## 어떻게 평가하나

```text
입력·기준 고정 → 프로젝트 1 전체 실행(A1→A2→A3→A4) → 증거 수집 → 역할별 채점 → 사람 대조
```

- **26개 기준 설계, 0~5점.** 기준마다 통과·위반 조건과 예시가 있습니다([세부 기준](docs/12_DETAILED_RUBRIC.md)). 대표 요청 5개에서 점수가 나온 것은 21개입니다. C02는 대표 요청에 역할을 넘는 지시가 없어 시험할 상황이 없었고, 단계 연결 4개는 채점 방법을 아직 완성하지 못했습니다.
- **코드 채점**: 집계·증거 연결·실행 기록 대조, 사람이 확정한 정답지가 있는 "시험 범위"는 프로그램이 직접 채점합니다. 예: 온도 범위가 20~30°C로 바뀌면 20·30 적용, 19·31 차단이 있어야 합니다.
- **AI 채점**: 나머지는 평가 AI가 근거 번호를 골라 판단합니다. 프로젝트 1이 OpenAI로 생성하므로, 채점은 다른 회사 모델인 **Claude Sonnet 5.5(생각 깊이 high)**가 기본입니다(2026-10-24부터). OpenAI로도 채점할 수 있습니다. 포트폴리오에 실린 대표 요청 5개 점수는 Claude로 바꾸기 전(10-23) OpenAI `gpt-6.1-sol`(생성과 같은 모델)로 채점한 결과입니다. Claude 채점은 답에 기준이 빠졌거나 이유가 한국어가 아니면 한 번만 다시 요청하고, 그래도 안 되면 오류로 남깁니다(OpenAI 채점은 다시 요청하지 않고 오류로 남김).
- **사람 대조**: AI 채점이 사람 판단과 맞는지 질문으로 확인하고, 틀린 부분은 기준을 고칩니다. 누적 27개 중 23개 일치이고, 틀린 4개는 모두 너무 후한 점수였습니다(10-17). 그 뒤 고친 기준으로 다시 대조한 12개는 모두 일치했습니다(10-23).

## 평가에서 찾은 문제를 고치는 절차

P2가 P1의 문제를 찾으면 사례마다 [개선 보고서](improvements/README.md)를 남깁니다. 순서는 발견 기록 → 맞는 감점인지 사람 확인 → 재발 확인 → 원인 조사 → 수정 방향 결정 → 수정·로컬 시험 → 비용 승인 후 실제 재검증 → 결론입니다. 예: [IMP-015](improvements/IMP-015-mode-restore-confirmation.md)는 온도 TC가 준비에서 바꾼 모드의 복원 확인을 빠뜨리는 문제(4번 중 4번)를 P1 계약까지 추적해 고쳤고, 같은 조건에서 3번 중 3번 포함으로 확인했습니다.

## 주요 명령

모든 명령은 저장소 루트에서 실행합니다. 처음 한 번 `python -m pip install -e ".[test,judge]"`를 실행합니다. 유료 호출이 있는 명령은 **기본이 미리보기**이고, 미리보기에 나온 승인 해시를 `--execute --approval-sha256`로 넘길 때만 실행됩니다.

```bash
# 전체 흐름: 프로젝트 1 실행 + 채점 (미리보기)
python -m eval_harness full-flow --target ../qa-agent-pipeline-v2 --spec benchmarks/v2/full_flow.temp-rep.json

# 저장된 실행을 다시 채점 (프로젝트 1을 다시 돌리지 않음)
python -m eval_harness rejudge-flow --flow evaluation-runs/<FLOW-...> --roles A1 A2 A3 A4

# 같은 저장 실행을 Claude로 채점 (모델 이름이 claude-로 시작하면 Claude 채점기, ANTHROPIC_API_KEY 필요)
# 기본 생각 깊이는 high. 다른 깊이는 이름 뒤에 붙임(예: claude-sonnet-5-5:medium).
# 2026-10-24부터 대표 시나리오 명세의 채점 기본 모델은 claude-sonnet-5-5(생성은 OpenAI, 채점은 다른 회사 모델).
# OpenAI로 채점하려면 --model gpt-6.1-sol.
python -m eval_harness rejudge-flow --flow evaluation-runs/<FLOW-...> --roles A1 A2 A3 A4 --model claude-sonnet-5-5

# 저장된 분석 결과로 시험 설계(A2)만 반복
python -m eval_harness repeat-a2 --flow evaluation-runs/<FLOW-...> --target ../qa-agent-pipeline-v2 --times 2

# 범위 정답지와 시험 설계 대조 (API 없음)
python -m eval_harness range-oracle --key benchmarks/v2/range_keys/representative-temperature.json --design <Run 폴더>/agent2_test_design.json

# 대시보드
python -m eval_harness dashboard --port 8766
```

대표 시나리오 5개(온도·모드·전원·풍량·잠금)의 실행 명세와 채점 정답 파일은 `benchmarks/v2/full_flow.*-rep.json`에 있습니다. 제품을 아직 바꾸지 않아 **실패가 나와야 정상**인 확인은 명세의 `expected_product_mismatches`에 미리 적어 두고, 예상대로 실패했는지를 따로 판정합니다.

## 포트폴리오

- [프로젝트 2 소개](https://sehooooon.github.io/agent-evaluation-framework/portfolio/index.html) · [평가 결과와 근거](https://sehooooon.github.io/agent-evaluation-framework/portfolio/report.html) · [기준별 점수표](https://sehooooon.github.io/agent-evaluation-framework/portfolio/scores.html) · [포트폴리오 메인](https://sehooooon.github.io/qa-agent-pipeline/main.html)
- 옛 디자인 시안과 백업은 [archive/](archive/README.md)에 있습니다.

## 문서

| 문서 | 내용 |
|---|---|
| [현재 상태와 검증 기록](PROJECT_HANDOFF.md) | 최신 실행 결과, 남은 일 |
| [세부 기준](docs/12_DETAILED_RUBRIC.md) · [Agent별 평가 명세](docs/08_AGENT_EVALUATION_SPEC.md) | 무엇으로 충족·위반·해당 없음을 판단하는지 |
| [실행 계획](docs/11_RELIABILITY_EVALUATION_PLAN.md) | 전체 실행 사용법, 호출 한도, 중단 조건 |
| [개선 이력](improvements/README.md) | 평가로 찾은 문제와 수정·재평가 증거 |
| [TC 정답지](benchmarks/v2/TC_REFERENCE.md) | 사람이 확정한 시험 작성 기준 |

## 한계

- 평가 AI도 틀릴 수 있습니다. 점수는 사람 대조와 함께 읽어야 합니다.
- 실행마다 비용이 듭니다(대표 시나리오 1개 전체 실행 약 $2: 프로젝트 1 약 $0.9 + Claude 채점 약 $1.1, 추정).
- 반복 횟수가 아직 적습니다(10-23 이후 전체 실행은 시나리오당 1회·온도 3회, 설계 반복은 3회·온도 6회). 반복 안정성은 단정하지 않습니다.
- 로컬 시험 통과는 실제 Agent 품질이나 반복 안정성을 뜻하지 않습니다.
- `evaluation-runs/`의 실제 실행 원본과 환경변수 파일은 Git에 포함하지 않습니다.
- 프로젝트 1의 원본·SRS·공식 TC는 바꾸지 않고, 외부 게시·사람 승인은 자동으로 하지 않습니다.
