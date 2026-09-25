# Project1 Agent Quality Evaluation Framework

## 저장소 사용 시 참고

코드·평가 기준·개선 이력을 관리하는 저장소입니다. `evaluation-runs/`의 실제 실행 원본과 환경변수 파일은 Git에 포함하지 않습니다. 문서에 연결된 로컬 Run·JUnit·대시보드의 저장 실행 목록은 새로 복제한 환경에 없을 수 있습니다. 결과가 없다는 이유로 예시 데이터를 실제 평가 결과로 표시하지 않습니다. 프로젝트 1의 최신 작업본과 평가 당시 고정 커밋도 구분합니다.

## 무엇을 평가하는가

프로젝트 2는 **프로젝트 1의 Agent 1~4 전체 흐름을 같은 조건에서 반복하고, 각 단계 결과와 연결을 평가**하기 위한 도구입니다. TC나 최종 보고서 하나만으로 전체 품질을 판단하지 않습니다.

평가 대상은 실제 모델 기반 `../qa-agent-pipeline-v2`입니다. `../portfolio_export`의 V1 Fixture 기반 Workflow Prototype은 초기 평가기 검증용 기준 자산으로 구분합니다.

```text
프로젝트 2: 입력·기준·시작 상태·예산 준비
  → 프로젝트 1: A1 분석 → A2 TC 설계 → A3 계획·코드·실제 시험 → A4 분류·보고
  → 프로젝트 2: A1 / A2 / A3 / A4 각각 평가 + Checkpoint·단계 연결 확인
  → 회차 결과 보존 → 같은 조건으로 다음 회차 → 예정 5회 비교·사람 검토
```

이 흐름의 **실행·수집·역할별 평가 연결기는 구현했고, 실제 API로 생성한 결과에서 기존 TC 재사용·실제 시험·Agent 4 보고까지 완료한 사례를 확보했습니다. 별도 Judge는 응답 검증 오류로 중단돼 의미 평가·평가자 교정·5회 실행은 미완료입니다.** 신규 TC 생성·Agent 3 모델 계획 경로의 전체 성공과는 구분합니다. 최신 호출 수와 중단 근거는 인계 문서에 기록합니다. 정상 경로의 Checkpoint와 관련 기존 TC 실행은 [실행 계획](docs/11_RELIABILITY_EVALUATION_PLAN.md)에 자세히 설명합니다. 전체 흐름이 주 시험이며 Agent 단독 시험은 문제의 원인을 분리하는 보조 시험입니다.

## 먼저 읽을 문서

평가에서 발견한 문제와 P1 수정·재평가 증거는 [평가 기반 개선 이력](improvements/README.md)에서 관리합니다. 발견 주체와 로컬 검사/실제 API 재평가를 구분하며 과거 실패 기록은 보존합니다.

| 문서 | 읽는 목적 |
|---|---|
| [현재 실행 계획](docs/11_RELIABILITY_EVALUATION_PLAN.md) | 전체 흐름 5회, 역할별 평가, 중단·재사용 분기, 호출 예산과 다음 순서 |
| [Agent별 평가 명세](docs/08_AGENT_EVALUATION_SPEC.md) · [세부 판정 기준](docs/12_DETAILED_RUBRIC.md) | 무엇을 근거로 충족·위반·미평가를 판단하는지 |
| [현재 상태와 검증 기록](PROJECT_HANDOFF.md) | 실제 테스트·실행 기록, 아직 남은 구현 |
| [구현 매핑](docs/05_IMPLEMENTATION_MAPPING.md) | 문서의 계획과 현재 코드 경로 구분 |

## 네 역할을 각각 확인

| 대상 | 평가 내용 |
|---|---|
| Agent 1 | 요구사항의 수치·변경/유지 조건·미정 범위·질문·분석 근거 |
| Agent 2 | 기존 TC 재사용 판단, 누락 없는 설계, [사용자 TC 기준](benchmarks/v2/TC_REFERENCE.md)의 상세한 조작·기대결과·복원 |
| Agent 3 | 모델 계획 → 컴파일 코드 → 실제 시험의 연결, 관찰 위치·시점·값, 복원·증거 |
| Agent 4 | 규칙 기반 결과 집계·실패 분류, 근거 연결, 미정/미실행·다음 행동 보고 |
| Checkpoint·전체 연결 | 잘못된 결과 차단과 정상 결과 과잉 차단, 인계 누락·변조·오류 전파·승인 경계 |

제품 시험 FAIL과 Agent 평가 FAIL은 다릅니다. 실제 불일치를 정확히 검출·보고했다면 해당 역할 기준은 충족할 수 있습니다. 중간 차단으로 실행되지 않은 역할은 미실행이며, 기존 TC 재사용으로 필요 없는 신규 계획 생성은 근거 있는 해당 없음입니다.

## 현재 구현 범위

2026-09-25 호환성 보완: P1의 생성·Checkpoint 재작성·근거 검토 호출을 모두 기존 P1 호출 한도에 포함합니다. 별도 P2 Judge 예산은 유지합니다. 신규 계획이 필요 없는 기존 TC 재사용은 계획 생성 기준만 ‘해당 없음’으로 처리하고 실제 시험·복원 증거는 계속 평가합니다. 현재 역할 패킷은 `role-rubric-2.1`이며 이전 패킷으로 새 평가를 실행하려면 다시 생성해야 합니다. 과거 결과는 수정하지 않습니다. 최신 P1 작업본과의 로컬 연결 검사는 실제 API 전체 실행 성공과 구분합니다.

| 경로 | 할 수 있는 일 | 아직 하지 않는 일 |
|---|---|---|
| `run-v2` | V2 저장 기록의 구조·해시·값·집계·일부 시나리오 조건 및 23개 상위 역할 표시 | 새 제품/모델 실행, 전체 의미 판정 |
| `repeat-self-test` | 모의 5회 호출·오류·중복·누락·집계 검사 | 프로젝트 1 실제 반복 실행 |
| `judge-openai` | 수동 준비한 단일 역할 패킷의 Preview, 승인 후 별도 모델 평가, 출력 계약 검사 | 전 단계 자동 수집·5회 실행 연결·E2E 패킷 |
| `full-flow` | 버전 고정·독립 사본·A1~4 연결·호출 한도·증거 수집·역할 평가·기록 비교 | P1 재사용 경로 Live 완료, Judge 검증 오류로 별도 의미 평가 미완료. 반복 일관성·사람 교정은 남음 |
| 대시보드 | 저장 V2 평가·전체 실행 기록·평가기 자체 검증을 구분해 조회 | 화면에서 유료 실행 시작 안 함 |
| 기존 `run`·`behavior-self-test` | V1 저장 Fixture와 합성 Trial로 평가기 자체 검사 | V2 Live 전체 흐름 및 실제 Agent 품질 입증 |

26개 세부 기준은 정의와 역할별 입력에 연결돼 있습니다. 필수 자료 키가 있다는 사실이나 평가기 테스트 통과를 실제 의미 정확성으로 해석하지 않습니다. 평가 모델의 사람 정답 교정과 전체 Live 반복 평가는 남아 있습니다.

## 현재 사용할 수 있는 명령

### 전체 흐름 Preview · 실제 호출 없음

```bash
python -m eval_harness full-flow --target ../qa-agent-pipeline-v2 --spec benchmarks/v2/full_flow.example.json
```

예시는 1회 리허설 명세입니다. P1 호출 최대 10회(생성·CP 재작성·근거 검토 합산), 별도 역할 평가 최대 4회이며 이 명령 자체는 전송하지 않습니다. 실제 전송·비용 승인 후 같은 명령에 `--execute --approval-sha256 PREVIEW_HASH`를 붙입니다. P1 요청당 출력 최대 12,000토큰, 평가 요청당 최대 6,000토큰이며 SDK 자동 재시도는 0입니다. 기존 명세의 `generation` 예산 필드에 근거 검토도 합산하며 프로젝트 1의 제한된 CP 재작성 정책은 유지합니다.

실행 기록은 `evaluation-runs/FLOW-.../`에 저장합니다. 5회 시험은 별도 명세와 예산 승인이 필요합니다. [전체 실행 사용법과 한계](docs/11_RELIABILITY_EVALUATION_PLAN.md#10-전체-실행-연결기-사용법)를 먼저 확인하세요.

최대 3회 제한 명세는 `full_flow.three-call-pilot.json`입니다. 생성·재작성·근거 검토 합산 최대 3회, Judge 0회로 전체 상한을 유지합니다. 이 한도로 전체 단계 완료를 보장하지 않습니다. 해당 파일로 바꿔 Preview를 확인할 수 있지만 새 실행에는 새 사용자 승인이 필요합니다. 이전 3회 승인을 재사용하지 않습니다.

### 역할별 평가 Preview

```bash
python -m pip install -e ".[judge]"
python -m eval_harness judge-openai --packet benchmarks/v2/role_packet.example.json --model MODEL_ID --role-rubric
```

`MODEL_ID`를 사용할 평가 모델 ID로 지정합니다. 명령 기본값은 전송 미리보기이며 API를 호출하지 않습니다. 예시 패킷은 합성 Agent 2 자료이고 실제 전달 이력이 없어 일부 증거 누락이 표시됩니다. 이를 Agent 2만 평가하는 전체 계획으로 해석하지 않습니다.

공통 2개를 포함해 A1 6개·A2 10개·A3 6개·A4 6개 기준을 적용합니다. 전체 연결기는 일부 E2E 코드 대조를 수행하며 의미 충분성·Checkpoint 오탐/미탐 평가는 남아 있습니다. 사용자 정의 일부 항목 패킷은 `--role-rubric` 없이 사용할 수 있고 `CUSTOM_SUBSET`으로 구분합니다. 실제 유료 호출은 전송 내용·예산 승인 후에만 수행합니다. [사용법과 한계](docs/11_RELIABILITY_EVALUATION_PLAN.md#8-openai-평가자-연결).

### 모의 반복 및 저장 기록 검사

```bash
python -m eval_harness repeat-self-test --trials 5 --scenario value-drift
python -m eval_harness run-v2 --target ../qa-agent-pipeline-v2
python -m eval_harness run-v2 --target ../qa-agent-pipeline-v2 --dataset benchmarks/v2/scenario_v1.jsonl
```

모의 시나리오는 `normal`, `value-drift`, `same-wrong`, `missing`, `transport-error`, `duplicate`입니다. 원본·호출 이벤트·오류와 JSON/HTML을 별도 `evaluation-runs/`에 보존합니다. 모의 명령 종료 0은 자체 시험 완료이지 Agent 품질 합격이 아닙니다.

저장 기록 경로는 과거 Run 3개를 읽으며 30개 시나리오 전체 실행이나 새 독립 Trial로 세지 않습니다. [명시 조건 검사](docs/10_SCENARIO_ORACLE.md)와 [TC 직접 검토](benchmarks/v2/TC_REFERENCE_REVIEW.md)는 한정된 근거입니다. `run-v2`는 자동 위반이 없어도 의미 검토가 남으면 REVIEW(종료 코드 3)이며 FAIL은 2, ERROR는 4입니다. 결과 HTML은 직접 열어 봅니다.

### 보존한 V1 평가기 검증 경로

```bash
python -m eval_harness behavior-self-test
python -m eval_harness run --suite project1-audit --mode fixture
python -m eval_harness run --suite known-good --mode fixture
python -m eval_harness dashboard --port 8766
```

대시보드 기본 포트는 8765이며 프로젝트 1 UI와 겹칠 수 있어 위 예시는 8766을 사용합니다. 첫 화면은 **저장 평가 요약 → 사례별 A1~4·전체 연결 → 펼치는 검사 근거** 순서입니다. 평가기 자체 검증은 별도 탭이며, 그 경로의 95%/90% Gate를 실제 Agent 품질 기준으로 승계하지 않습니다. [화면 사용법](docs/06_DASHBOARD_GUIDE.md)을 참고하세요. 개발 Case 목록과 한계는 [Benchmark Card](docs/03_BENCHMARK_CARD.md)를 확인하세요.

## 다음 구현 순서

1. 독립 정답 초안·전송 Preview·호출 예산을 검토하고 실제 1회 리허설 승인
2. 새 실행의 A1~4 자료·26개 기준 판정을 사람과 대조해 평가자 교정
3. 조건 고정 후 전체 흐름 5회와 역할별·연결 결과 비교
4. 발견 문제의 단독 재현과 별도 승인된 개선 전후 비교

전체 흐름 5회는 API 5회가 아닙니다. 생성·후보별 재작성·평가 호출의 예산을 나눠 승인받습니다. 단독 생성 5회+평가 5회의 예산을 전체 실행에 그대로 사용하지 않습니다.

## 문서와 보호 범위

현재 순서는 [신뢰성 검증 계획](docs/11_RELIABILITY_EVALUATION_PLAN.md)이 우선합니다. [기획서](docs/00_PROJECT_PLAN.md), [계약](docs/01_AGENT_CONTRACTS.md), [평가 명세](docs/02_EVALUATION_SPEC.md)는 같은 목표를 각 관점에서 설명합니다. [초기 저장 검사 베이스](docs/07_V2_EVALUATION_BASE.md)와 [과거 재평가](docs/09_PROJECT1_REVIEW.md)는 역사적 기록이며 현재 성능으로 합산하지 않습니다.

프로젝트 1 원본·SRS·공식 TC·포트폴리오는 변경하지 않습니다. 반복 시험 중 Notion/Slack 게시·사람 승인·공식 등록은 자동 수행하지 않습니다. 생성 결과·평가 결과·제품 시험·사람 승인 권한을 분리합니다.
