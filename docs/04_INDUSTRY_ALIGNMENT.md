# Agent 평가 방식 대조

문서 ID: `ALIGN-001`  
문서 버전: `v0.3`

문서 범위 정리: 2026-09-20. 외부 자료 대응을 보존한 설명이며 현재 구현·실행 순서는 [FLOW-v1.0](11_RELIABILITY_EVALUATION_PLAN.md)이 기준입니다. 현재 주 시험은 V2 전체 흐름 5회와 매 회차 A1~4·Checkpoint·연결 평가입니다. 아래 외부 자료를 이번 문서 정합성 작업에서 새로 검증했다는 뜻은 아닙니다.

## 1. 결론

Agent 평가 방법이 하나의 고정된 표준으로 통일돼 있지는 않습니다. 그러나 다음 골격은 여러 공식 가이드에서 반복됩니다.

```text
역할·성공 조건 정의
→ 평가 Task/Case 작성
→ 반복 Trial과 Trace 수집
→ 결정론적·참조·Rubric Grader
→ Human Review와 Calibration
→ 버전 비교와 Regression
```

프로젝트 2는 이 구조를 프로젝트 1 규칙 준수·안정성·Grounding 평가에 맞게 축소했습니다.

## 2. 본 프로젝트의 대응

| 일반 Eval 개념 | 프로젝트 2 |
|---|---|
| Task | Benchmark Case |
| Trial | 동일 조건의 Agent 또는 실행기 1회 수행 |
| Trace | 입력·원본 출력·정규화 결과·Rule Result·Evidence |
| Grader | Schema·Reference·Rubric·Aggregate 검사 |
| Outcome | Fact·Gap·TC·Assert·실행·Release 핵심 결과 |
| Evaluation Suite | Development·Calibration·Holdout·Regression |

## 3. Anthropic 자료와의 대응

Anthropic은 Task, Trial, Grader, Transcript/Trace, Outcome과 Evaluation Harness를 구분하고 비결정적 Agent는 여러 Trial로 평가할 것을 설명합니다.

본 프로젝트는 저장 Fixture를 반복해서 읽는 대신 실제 Agent 호출이 있는 Live 모드에서만 안정성을 측정합니다.

참고: [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)

## 4. Microsoft 자료와의 대응

Microsoft는 전체 Task 완료를 보는 System Evaluation과 Tool 선택·입력·실행 같은 Process Evaluation을 구분합니다.

- System 관점: Project1 규칙에 맞는 최종 Fact·TC·실행·Release 결과
- Process 관점: 역할 경계, 추적성, Checkpoint 차단, Source Run·Evidence 연결

참고: [Agent Evaluators for Generative AI](https://learn.microsoft.com/en-us/azure/foundry/concepts/evaluation-evaluators/agent-evaluators)

작은 핵심 Test Set에서 Baseline을 만들고 회귀 Suite로 확장하는 방향을 참고합니다. 현재 Development 9개 설계 Case, 저장 seed, 실제 전체 흐름 시험용 정답 준비는 서로 구분하며 Calibration·Holdout 성능은 아직 측정하지 않았습니다.

참고: [Agent Evaluation Checklist](https://learn.microsoft.com/en-us/agents/agent-evaluation/evaluation-checklist)

## 5. NIST AI RMF와의 대응

NIST AI RMF는 평가 세트·측정 방식·도구·한계의 문서화, 반복 가능성과 투명한 위험 관리를 강조합니다.

- Rule·Dataset·Grader 버전 기록
- 실제 실행 여부와 적용 불가 범위 공개
- Human Review와 평가기 오류 분리
- Critical Hard Gate
- Project1 원본 보호와 Evidence 보존

참고: [NIST AI RMF Core](https://airc.nist.gov/airmf-resources/airmf/5-sec-core/)

## 6. 프로젝트 고유 설계

- Project1 Prompt와 3-Tier QA 기준을 Rule Catalog로 사용
- Agent 1은 Fact·Gap·Proceed, Agent 2는 TC·Assert·승인을 핵심 Signature로 비교
- 환각을 unsupported confirmed claim으로 구체화
- Critical 위반은 평균 점수로 상쇄하지 않음
- V2 A3는 모델 계획·결정론적 컴파일·실제 시험을 나눠 평가하고, A4는 규칙 기반 집계·분류·보고를 평가
- 제품 Release와 Evaluation Gate를 분리

95% 준수율, 90% 안정성과 Case별 3·5회 Trial은 업계 표준이 아니라 초기 합성 경로 정책입니다. 현재 전체 흐름은 시나리오별 5회 시도이며 초기 수치 Gate를 최종 합격 기준으로 승계하지 않습니다.

## 7. 과장하지 않는 표현

| 피해야 할 표현 | 권장 표현 |
|---|---|
| Agent 품질을 완벽히 검증 | 정의된 Project1 Benchmark 범위에서 평가 |
| 네 AI Agent를 모두 실제 반복 실행 | A4는 규칙 기반이며, P2 전체 반복 연결은 무과금 검증 상태. 실제 모델 반복은 미실행 |
| Fixture로 안정성 입증 | 안정성은 실제 동일 조건 Trial에서만 측정 |
| 객관적 절대 점수 | 사전 정의 Rule·Reference·Gate에 따른 범위 한정 지표 |
| Agent 4 분류 정확도 검증 | 제한된 행·집계 검사는 구현, 독립 중립 분류 정답·정확도 검증은 미완료 |

## 8. 포트폴리오 설명 문장

> 프로젝트 1에 정의한 QA 규칙을 Rule Catalog와 Benchmark Case로 고정하고, 동일 입력 반복 Trial에서 규칙 준수율·핵심 결과 안정성·근거 없는 생성과 Critical 위반을 평가하는 Harness를 설계했습니다. 현재는 합성 Trial과 Known-good/bad로 평가기 자체를 검증했으며, 실제 Agent 성능은 Live Adapter 연결 후 별도 측정하도록 경계를 분리했습니다.
