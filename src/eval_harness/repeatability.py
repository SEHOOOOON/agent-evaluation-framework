"""Budgeted repeat-evaluation core. Built-in transport is simulation ONLY.

No SDK import, credentials, network, target execution, or approval side effects.
Selected contract values are compared, not claimed to be full semantic equivalence.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .scenario_oracle import select, subset


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode("utf-8")).hexdigest()


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    criterion_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    basis: str = Field(min_length=1)
    path: list[str] = Field(min_length=1)
    operator: Literal["equals", "contains", "count", "not_contains"]
    expected: Any
    critical: bool = True
    # Order independence is opt-in; action sequence must retain its order.
    unordered: bool = False

    @model_validator(mode="after")
    def valid_count(self):
        if self.operator == "count" and (type(self.expected) is not int or self.expected < 0):
            raise ValueError("Count requires a non-negative integer")
        return self


class RepeatPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(min_length=1)
    agent_id: Literal["A1", "A2", "A3", "A4"]
    target_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model_config_id: str = Field(min_length=1)
    input_payload: dict[str, Any]
    planned_trials: int = Field(default=5, ge=1, le=100)
    call_limit: int = Field(default=5, ge=1, le=100)
    criteria: list[Criterion] = Field(min_length=1)
    oracle_status: Literal["DRAFT", "HUMAN_REVIEWED"] = "DRAFT"
    manual_review_topics: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_criteria(self):
        ids = [c.criterion_id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate criterion ID")
        canonical(self.model_dump())
        return self


def grade(output, criteria):
    checks, values = [], {}
    for rule in criteria:
        try:
            selected = select(output, rule.path)
            if rule.unordered:
                selected = sorted(selected, key=canonical)
            values[rule.criterion_id] = selected
            if rule.operator == "count":
                valid = len(selected) == rule.expected
            elif rule.operator == "equals":
                valid = len(selected) == 1 and canonical(selected[0]) == canonical(rule.expected)
            else:
                found = any(subset(item, rule.expected) for item in selected)
                valid = found if rule.operator == "contains" else not found
            state = "PASS" if valid else "FAIL"
            reason = "조건 충족" if valid else "기준 값/범위 불일치"
        except KeyError:
            # In this contract the path is a required output field, not an
            # optional artifact to be skipped from the denominator.
            state, reason = "FAIL", "필수 출력 항목 누락"
            values[rule.criterion_id] = {"missing": True}
        except (TypeError, ValueError):
            state, reason = "ERROR", "출력 형식 또는 비교 계약 해석 오류"
            values[rule.criterion_id] = {"comparison_error": True}
        checks.append(dict(criterion_id=rule.criterion_id, objective=rule.objective,
                           status=state, critical=rule.critical, reason=reason))
    return checks, values


class SimulationTransport:
    """One call consumes one provided synthetic response. Never retries."""
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def invoke(self, payload, trial_id):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def run_simulated(plan: RepeatPlan, transport: SimulationTransport, output_root: Path):
    """Persist every attempt including errors. This is NOT a live API runner."""
    if type(transport) is not SimulationTransport:
        raise ValueError("Only the built-in no-network simulation transport is supported")
    plan = RepeatPlan.model_validate(plan.model_dump())
    root = output_root / ("REPEAT-SELFTEST-" + uuid4().hex[:12])
    root.mkdir(parents=True, exist_ok=False)
    baseline = dict(mode="SIMULATED", plan=plan.model_dump(),
                    plan_sha256=fingerprint(plan.model_dump()), input_sha256=fingerprint(plan.input_payload),
                    evaluator_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                    comparison_module_sha256=sha256(Path(__file__).with_name("scenario_oracle.py").read_bytes()).hexdigest(),
                    network_requests=0, target_executed=False, sdk_retries=0, correction_calls=0)
    (root / "baseline.json").write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    trials, seen_responses = [], set()
    started = 0
    ledger = root / "attempts.jsonl"

    def event(item):
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(canonical(item) + "\n")
            stream.flush()

    for number in range(1, plan.planned_trials + 1):
        trial_id = f"T{number:03d}"
        if started >= plan.call_limit:
            trials.append(dict(trial_id=trial_id, status="NOT_RUN", reason="CALL_LIMIT", checks=[]))
            continue
        started += 1
        event(dict(trial_id=trial_id, event="STARTED", attempt=started,
                   input_sha256=baseline["input_sha256"]))
        try:
            envelope = transport.invoke(deepcopy(plan.input_payload), trial_id)
            if not isinstance(envelope, dict) or set(envelope) != {"response_id", "output"}:
                raise ValueError("Invalid response envelope")
            rid = envelope["response_id"]
            if not isinstance(rid, str) or not rid.strip() or rid in seen_responses:
                raise ValueError("Missing or reused response identity")
            seen_responses.add(rid)
            output = envelope["output"]
            if not isinstance(output, dict):
                raise ValueError("Output must be an object")
            canonical(output)
            artifact = f"{trial_id}.json"
            (root / artifact).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
            checks, core = grade(output, plan.criteria)
            status = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "ERROR" if any(c["status"] == "ERROR" for c in checks) else "PASS"
            record = dict(trial_id=trial_id, response_id=rid, status=status, checks=checks,
                          output_file=artifact, output_sha256=fingerprint(output),
                          artifact_sha256=sha256((root / artifact).read_bytes()).hexdigest(),
                          contract_values=core, contract_sha256=fingerprint(core))
        except Exception as exc:
            # Transport errors can embed keys/paths. Never persist raw messages.
            record = dict(trial_id=trial_id, status="ERROR", reason=type(exc).__name__, checks=[])
        trials.append(record)
        event(dict(trial_id=trial_id, event="FINISHED", status=record["status"]))

    counts = Counter(t["status"] for t in trials)
    comparable = [t for t in trials if "contract_sha256" in t and not any(c["status"] == "ERROR" for c in t["checks"])]
    raw_counts = Counter(t["output_sha256"] for t in comparable)
    core_counts = Counter(t["contract_sha256"] for t in comparable)
    contract_consistency = max(core_counts.values()) / len(comparable) if comparable else None
    per_rule = []
    for criterion in plan.criteria:
        states = Counter(c["status"] for t in trials for c in t["checks"] if c["criterion_id"] == criterion.criterion_id)
        per_rule.append(dict(criterion_id=criterion.criterion_id, objective=criterion.objective,
                             **{s.lower(): states[s] for s in ("PASS", "FAIL", "ERROR")},
                             unassessed=plan.planned_trials-sum(states.values()),
                             affected_trials=[t["trial_id"] for t in trials if not t["checks"] or any(c["criterion_id"] == criterion.criterion_id and c["status"] != "PASS" for c in t["checks"])]))
    result = dict(mode="SIMULATED", target_executed=False, api_requests=0,
                  planned_trials=plan.planned_trials, started_trials=started, simulated_calls=started,
                  valid_outputs=sum("output_file" in t for t in trials),
                  pass_count=counts["PASS"], fail_count=counts["FAIL"], error_count=counts["ERROR"], not_run_count=counts["NOT_RUN"],
                  compliance_rate=counts["PASS"] / plan.planned_trials,
                  comparable_trials=len(comparable), contract_consistency_rate=contract_consistency,
                  raw_output_consistency_rate=max(raw_counts.values())/len(comparable) if comparable else None,
                  all_planned_trials_comparable=len(comparable)==plan.planned_trials,
                  assessment="FAIL" if counts["FAIL"] else "INCOMPLETE" if counts["ERROR"] or counts["NOT_RUN"] else "REVIEW",
                  oracle_status=plan.oracle_status, manual_review_topics=plan.manual_review_topics,
                  performance_claim_eligible=False, criteria=per_rule, trials=trials,
                  note="합성 호출로 반복 평가기를 검사한 결과. 실제 Agent 성능이 아님. 계약 값 일관성은 전체 TC 의미 동등성·정답을 보장하지 않음.")
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = ''.join('<tr><td>'+escape(t['trial_id'])+'</td><td>'+t['status']+'</td><td>'+escape(', '.join(c['objective'] for c in t['checks'] if c['status']!='PASS') or t.get('reason',''))+'</td></tr>' for t in trials)
    rules = ''.join('<tr><td>'+escape(r['objective'])+'</td><td>'+str(r['pass'])+'</td><td>'+str(r['fail'])+'</td><td>'+str(r['error']+r['unassessed'])+'</td></tr>' for r in per_rule)
    consistency_text = "미측정" if contract_consistency is None else f"{contract_consistency:.0%}"
    html = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>반복 평가기 검증</title><style>body{max-width:1000px;margin:40px auto;padding:24px;font:16px/1.7 sans-serif;background:#0b1018;color:#d5e1f0}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #334155}aside{padding:20px;background:#172333}</style><h1>프로젝트 2 · 반복 평가기 검증</h1><aside>'+escape(result['note'])+'</aside><p>예정 '+str(plan.planned_trials)+'회 · 모의 호출 '+str(started)+'회 · 실제 API 0회</p><p>기준 충족 '+str(counts['PASS'])+'/'+str(plan.planned_trials)+' · 실패 '+str(counts['FAIL'])+' · 오류 '+str(counts['ERROR'])+' · 미실행 '+str(counts['NOT_RUN'])+'</p><p>선택된 계약 값 일관성 '+consistency_text+' · 비교 가능 '+str(len(comparable))+'/'+str(plan.planned_trials)+'회. 일관된 오답도 가능하므로 기준 충족과 별개입니다.</p><h2>회차별 결과</h2><table><tr><th>회차</th><th>상태</th><th>문제 항목</th></tr>'+rows+'</table><h2>기준별 결과</h2><table><tr><th>기준</th><th>충족</th><th>위반</th><th>오류·미검사</th></tr>'+rules+'</table><h2>사람 검토가 남은 항목</h2><ul>'+''.join('<li>'+escape(t)+'</li>' for t in plan.manual_review_topics)+'</ul></html>'
    (root / "report.html").write_text(html, encoding="utf-8")
    return root


def run_self_test(output_root: Path, scenario: str, trials: int = 5):
    """Small explicit synthetic contract; not an actual Project 1 TC sample."""
    if scenario not in {"normal", "value-drift", "same-wrong", "missing", "transport-error", "duplicate"}:
        raise ValueError("Unknown simulation scenario")
    plan = RepeatPlan(case_id="SELF-A2-TEMPERATURE", agent_id="A2", target_version="SYNTHETIC",
        prompt_version="SYNTHETIC", model_config_id="NO_MODEL", planned_trials=trials, call_limit=trials,
        input_payload={"fixture_note":"평가기 검증용 구조화 예시. 실제 프로젝트 1 실행 입력이 아님",
                       "requested_temperature_c":18,"restore_required":True},
        manual_review_topics=["주신 TC 수준의 조작 순서·관찰 위치·기대결과 상세도", "문장 의미와 전체 요구사항 검증 범위"],
        criteria=[
            Criterion(criterion_id="A2-VALUE",objective="요청한 온도 18도 보존",basis="합성 입력의 명시 값",
                      path=["test_cases","*","test_data","requested_temperature_c"],operator="equals",expected=18),
            Criterion(criterion_id="A2-RESTORE",objective="복원 필요 선언 보존",basis="합성 입력의 복원 조건",
                      path=["test_cases","*","restore_required"],operator="equals",expected=True)])
    responses=[]
    for index in range(trials):
        output={"title":f"표현만 다른 예시 {index+1}","test_cases":[
            {"test_data":{"requested_temperature_c":18},"restore_required":True}]}
        if scenario=="same-wrong" or (scenario=="value-drift" and index==trials-1):
            output["test_cases"][0]["test_data"]["requested_temperature_c"]=16
        if scenario=="missing" and index==trials-1:
            del output["test_cases"][0]["restore_required"]
        rid="fake-0" if scenario=="duplicate" else f"fake-{index}"
        response=dict(response_id=rid,output=output)
        if scenario=="transport-error" and index==trials-1:
            response=TimeoutError("Synthetic timeout")
        responses.append(response)
    return run_simulated(plan,SimulationTransport(responses),output_root)
