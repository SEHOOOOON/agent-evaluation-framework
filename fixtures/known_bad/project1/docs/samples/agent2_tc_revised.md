# Agent 2 승인 테스트케이스

```yaml
test_cases:
  - tc_id: "TC-MODE-001"
    title: "단일 장비 모드 전환"
    requirement_ids: ["REQ-001"]
    preconditions: ["시뮬레이터 초기화"]
    steps: ["장치 1 선택", "HEAT 적용"]
    review_status: "Approved"
    excluded_assertions: []
    unchanged_state_assertions: []

  - tc_id: "TC-INT-002"
    tc_category: "엣지케이스"
    title: "복수 장비 일괄 제어"
    requirement_ids: ["REQ-002"]
    preconditions: ["장치 1~3 선택"]
    steps: ["HEAT 일괄 적용"]
    review_status: "Approved"
    excluded_assertions:
      - "비대상 장비(4~16)의 실행 전·후 Snapshot 비교는 V2 범위"
    unchanged_state_assertions: []
```

