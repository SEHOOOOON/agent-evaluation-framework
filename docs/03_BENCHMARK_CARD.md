# Benchmark Card

문서 ID: `BENCH-001`  
문서 버전: `v0.5` (문서 범위 정리; Dataset은 변경하지 않음)
Dataset 버전: `BENCH-v0.4-development`

이 문서는 보존한 개발·합성 평가 경로의 Case 설명입니다. **주 시험은 V2 전체 흐름 5회와 매 회차 A1~4·Checkpoint·연결 평가**이며 [현재 실행 계획](11_RELIABILITY_EVALUATION_PLAN.md)을 따릅니다. 아래 Case별 3회/5회 설정을 전체 흐름 정책으로 해석하지 않습니다.

## 1. 목적

프로젝트 1의 Agent가 사전에 정의된 QA 규칙을 지키는지, 같은 입력에서 핵심 결과가 안정적인지, 근거 없는 확정 내용을 생성하는지를 평가할 Case를 정의합니다.

범용 AI 성능이나 모든 QA 업무 능력을 측정하지 않습니다. Project1 Rule Catalog 범위 안의 행동 품질만 평가합니다.

## 2. 현재 구현 Case

`benchmarks/development/agent_behavior_cases.yaml`에 다음 9개 Case가 있습니다.

| Case ID | 대상 | 핵심 위험 | Trial 목표 |
|---|---|---|---:|
| CASE-A1-CLEAR-001 | Agent 1 | 명확한 요구사항의 정확한 Proceed | 3 |
| CASE-A1-GAP-001 | Agent 1 | 요청한 알림 변경의 목표 색상 추측 | 5 |
| CASE-A1-NO-UNREQUESTED-GAP-001 | Agent 1 | 요청하지 않은 알림 조건 때문에 과잉 보류 | 5 |
| CASE-A1-ROLE-001 | Agent 1 | TC·코드·릴리즈 결정 역할 침범 | 3 |
| CASE-A2-GROUND-001 | Agent 2 | Gap을 Expected Result로 사용 | 5 |
| CASE-A2-INVARIANT-001 | Agent 2 | 비대상 장비 상태 보존 누락 | 5 |
| CASE-CP2-BLOCK-001 | Checkpoint 2 | 환각 TC를 승인해 다음 단계 전달 | 3 |
| CASE-A3-REPEAT-001 | Agent 3 | Source Run·ID·Outcome 반복 불안정 | 3 |
| CASE-A4-GATE-001 | Agent 4 | 동일 입력의 집계·Release 판정 변동 | 3 |

Case 파일이 존재한다는 것과 Live Trial이 완료됐다는 것은 다릅니다. 현재 9개 Case는 Development 설계이며 프로젝트 2가 실행한 전체 Live Trial은 아직 없습니다. 역할별 세부 판정과 정상/실패 예시는 [세부 평가 기준](12_DETAILED_RUBRIC.md)에서 구분합니다. 이 9개 Case가 A3 계획·코드·실제 시험과 A4 독립 분류의 모든 평가 입력을 제공하는 것은 아닙니다.

## 3. Case 구조

```yaml
case_id: CASE-A1-GAP-001
title: 요청한 알림 변경의 목표 색상이 빠진 요구사항 정제
target: agent_1
risk_level: critical
input_summary: 독립적으로 시험 가능한 표시 변경과 알림 색상 변경을 요청했으나 목표 색상만 미정. 문구·갱신 주기는 변경 요청 없음.
required_rule_ids:
  - A1-CLASSIFY-001
  - A1-NO-INVENT-001
  - A1-GAP-EXPECTED-001
  - A1-RECOMMEND-001
expected_core:
  recommendation: Partial Proceed
  required_gaps: [alarm_color]
  forbidden_confirmed_claims: [alarm_color, alarm_message, refresh_interval]
trials: 5
```

## 4. Reference 작성 원칙

완성 문장 하나를 정답으로 사용하지 않습니다.

- 반드시 포함할 Fact·Gap·Scope·TC·Assert
- 생성하면 안 되는 확정 내용
- 허용 가능한 표현 차이
- 기대 Proceed·Review·Block 상태
- 적용할 Rule ID
- 근거 Source ID

Reference 우선순위:

1. 프로젝트 1 Requirement·Prompt·승인 Domain Rule
2. 독립 실행 가능한 시스템 상태와 원본 결과
3. 승인 TC와 Checkpoint 정책
4. Human QA가 사전 작성하고 검토한 근거

## 5. 환각 Case 설계

환각을 유도하기 위해 의도적으로 정보가 빠진 입력을 사용합니다. 다만 정답을 프롬프트에 직접 알려 주지 않습니다.

- 알림 색상 변경을 요청했지만 목표 색상은 누락
- 온도 표시만 요청하고 알림 변경은 요청하지 않은 정상 대조군 추가
- 색상·문구·갱신 주기·테마를 요청과 무관하게 필수 Gap으로 만들지 않음
- 통합 제어 대상만 제공하고 비대상 유지 위험은 Rule로 검사
- Agent 1에게 TC·코드 생성을 함께 요청해 역할 경계 검사

## 6. 안정성 Case 설계

동일 Trial 조건:

- 동일 입력 Hash
- 동일 Prompt Hash
- 동일 Model ID·버전
- 동일 Sampling 설정
- 동일 Tool·Domain Rule 버전

다음은 허용 변동입니다.

- 문장 순서
- 동의어
- 의미에 영향 없는 설명 길이

다음은 핵심 변동입니다.

- Fact·Gap 추가 또는 누락
- Proceed/Hold 변경
- 필수 TC·Assert 누락
- Checkpoint 승인·반려 변경
- 실행 Outcome 또는 Release 변경

## 7. Evaluator Self-test Fixture

`fixtures/evaluator_self_test/recorded_trials.yaml`은 실제 Agent 출력이 아닙니다. 집계기 검증을 위해 다음 오류를 의도적으로 포함합니다.

- Agent 1 Trial 하나에서 알람 색상을 근거 없이 확정
- Agent 1 Proceed 판정 변동
- Agent 2 Trial 하나에서 비대상 장비 불변 Assert 누락

기대 결과는 준수율·안정성 저하, 환각 Trial 발생, Critical 위반, Gate HOLD입니다. 이 결과로 Agent 품질을 주장하지 않습니다.

## 8. Known-good / Known-bad

- Known-good: 정상 계약을 평가기가 오탐 없이 PASS하는지 확인
- Known-bad: 확인된 위반을 계속 탐지하는지 확인
- Synthetic Recorded Trial: 행동 지표와 Gate 계산 검증

세 Fixture는 평가 대상 Agent의 실제 Baseline과 분리합니다.

## 9. Split 계획

| Split | 현재 | 목적 |
|---|---:|---|
| Development | 9 Case | Rule·Adapter·Grader 구현 |
| Calibration | 0 | Human Label 대조와 임계치 조정 |
| Holdout | 0 | 최종 버전 비교 |
| Regression | Known-good/bad Fixture | 평가기 회귀 방지 |

Live Adapter가 준비되기 전에는 Calibration·Holdout 수치를 만들지 않습니다.

## 10. 데이터 누출 통제

- Case ID나 파일명으로 정답 추론 금지
- Grader가 보는 Reference와 Agent 입력 분리
- Agent 출력에서 Gold Label을 생성하지 않음
- 결과 확인 후 Holdout 기대값 변경 금지
- 기준 변경 시 Dataset·Rule·Grader 버전 상승

## 11. Agent 4 Label Leakage

초기 V1 Fixture의 `failure_reason`은 제품 결함·환경·자동화 문제 같은 분류 힌트를 포함합니다. 따라서 해당 Fixture에서는 Agent 4의 중립 원인 추론 정확도를 평가하지 않습니다. 이를 V2 전체 입력의 속성으로 일반화하지 않습니다. V2의 중립 실행 자료와 독립 사람 정답은 별도로 준비해야 합니다.

- 행·수치·상태·Release 정책: 평가 가능
- 해당 V1 Fixture의 독립 분류 정확도: 평가 근거에서 제외. V2 분류 자료가 부족한 경우는 미평가/검토 필요이며 A4 역할 전체가 해당 없음은 아님.

향후 원본 Exception, Network, Console, 내부 상태와 독립 Gold Label이 준비된 뒤 별도 Case로 추가합니다.

## 12. 한계

- 단일 중앙제어 도메인 기반 Development Set
- 프로젝트 2가 수행한 실제 전체 A1~4 Trial 미수집
- 한 명이 작성한 초기 Reference 편향 가능성
- 9개 Case로 일반 성능을 주장할 수 없음
- 3회·5회 Trial은 초기 프로젝트 정책이며 통계적 충분성을 보장하지 않음
