# Agent 2 승인 테스트케이스 정상 기준본

```yaml
test_cases:
  - tc_id: "TC-MODE-001"
    tc_category: "해피패스"
    title: "단일 장비 모드 전환"
    requirement_ids: ["REQ-001"]
    source_evidence: ["FACT-001"]
    qa_criteria: ["UI와 장치 상태 일치"]
    quality_standard_ids: ["QA-COMMON-001"]
    risk_covered: ["표시 상태와 내부 상태 불일치"]
    initial_state: ["장치 1 COOL"]
    preconditions: ["시뮬레이터 초기화"]
    setup: ["장치 1 선택"]
    steps: ["HEAT 모드 적용"]
    expected_ui: ["장치 1이 HEAT로 표시"]
    expected_device_state: ["장치 1 mode=HEAT"]
    negative_assertions: ["제어 오류 미표시"]
    unchanged_state_assertions: ["장치 2~16 상태 유지"]
    excluded_assertions: []
    cleanup: ["초기 상태 복원"]
    double_assert_policy: "UI와 장치 상태를 함께 확인"
    automation_level: "E2E"
    independent_execution: true
    review_status: "Approved"

  - tc_id: "TC-INT-002"
    tc_category: "엣지케이스"
    title: "복수 장비 일괄 제어"
    requirement_ids: ["REQ-002"]
    source_evidence: ["FACT-002"]
    qa_criteria: ["선택 장비만 변경", "비대상 장비 상태 유지"]
    quality_standard_ids: ["QA-DOMAIN-002"]
    risk_covered: ["비대상 장비 상태 오염"]
    initial_state: ["장치 1~16 COOL"]
    preconditions: ["시뮬레이터 초기화"]
    setup: ["장치 1~3 선택", "장치 4~16 Snapshot 저장"]
    steps: ["HEAT 일괄 적용"]
    expected_ui: ["장치 1~3 HEAT 표시"]
    expected_device_state: ["장치 1~3 mode=HEAT"]
    negative_assertions: ["장치 4~16 제어 요청 미발생"]
    unchanged_state_assertions: ["장치 4~16 실행 전·후 Snapshot 동일"]
    excluded_assertions: []
    cleanup: ["초기 상태 복원"]
    double_assert_policy: "UI와 장치 상태를 함께 확인"
    automation_level: "E2E"
    independent_execution: true
    review_status: "Approved"
```
