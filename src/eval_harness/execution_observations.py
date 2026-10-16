"""Read-only plan/observation comparison, not a source-code or semantic judge.

The compiler's reader/selector implementation is covered by local P1 tests.
These records are evidence from that implementation, not independent telemetry.
Missing old-format records are REVIEW; a correctly detected product mismatch
does not lower the agent's execution-fidelity score.
"""
import json

from .v2_recorded import child, digest, read

VERSION = "assertion-observations-1.0"
OBSERVED = "QA_ASSERTION_OBSERVED: "
COMPLETE = "QA_ASSERTIONS_COMPLETE: "
# P1 writes this first line when it keeps only the tail of a long trial log.
TRUNCATED = "QA_OUTPUT_TRUNCATED: "


def judgment(verdict, reason, evidence=()):
    return dict(criterion_id="A3-03", verdict=verdict,
                score=5 if verdict == "PASS" else 0 if verdict == "FAIL" else None,
                reason=reason, evidence=list(evidence))


def equal(actual, expected):
    # bool is not a numeric observation, despite Python's True == 1.
    if type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def matches(assertion, actual):
    strategy = assertion["strategy"]
    if strategy in {"UI_TEMPERATURE", "INTERNAL_SET_TEMP"}:
        return equal(actual, assertion["expected_number"])
    if strategy in {"CONTROLLER_UI_FIELDS_EQUALS", "INTERNAL_DEVICE_FIELDS_EQUALS"}:
        return equal(actual, {f["field_name"]: f["expected_value"] for f in assertion["expected_fields"]})
    if strategy == "UI_TEXT_CONTAINS":
        return isinstance(actual, str) and assertion["expected_text"] in actual
    if strategy == "UI_VALUE_EQUALS":
        return equal(actual, str(assertion["expected_value"]))
    if strategy in {"UI_CHECKED_EQUALS", "UI_ENABLED_EQUALS", "INTERNAL_VALUE_EQUALS"}:
        return equal(actual, assertion["expected_value"])
    if strategy == "TOAST_VISIBLE":
        return actual is True
    if strategy == "CONTROLS_DISABLED":
        return type(actual) is list and len(actual) == 2 and all(v is False for v in actual)
    if strategy == "DISABLED_TEMPERATURE_TEXT":
        return isinstance(actual, str) and "---" in actual
    # Do not create another dictionary of notification words in the evaluator.
    raise ValueError("Unsupported legacy observation comparison")


def compare(case, plan, log, *, run_id, outcome, sink=None):
    """Grade only complete, interpretable product-assertion records.

    ``sink`` receives the records only when they fully verify (PASS).
    """
    try:
        events = [json.loads(line[len(OBSERVED):]) for line in log.splitlines() if line.startswith(OBSERVED)]
        ends = [json.loads(line[len(COMPLETE):]) for line in log.splitlines() if line.startswith(COMPLETE)]
        identity = dict(version=VERSION, run_id=run_id, tc_id=case["tc_id"])
        if not events or not ends:
            return judgment("REVIEW", "본시험의 구조화 관찰 기록 또는 완료 기록 없음; 과거·중단 기록을 통과로 추정하지 않음")
        if len(ends) != 1 or ends[0] != identity or plan.get("tc_id") != case["tc_id"]:
            return judgment("FAIL", "관찰 기록의 실행·TC 또는 완료 기록 중복 불일치")
        assertions = plan["assertions"]
        expected_ids = [a["result_id"] for a in assertions]
        tc_ids = [r["result_id"] for r in case["expected_results"]]
        actual_ids = [e["assertion"]["result_id"] for e in events]
        if (not expected_ids or len(set(expected_ids)) != len(expected_ids)
                or len(set(tc_ids)) != len(tc_ids) or set(expected_ids) != set(tc_ids)
                or len(set(actual_ids)) != len(actual_ids)):
            return judgment("FAIL", "TC·계획·실제 관찰의 필수 검사 번호 누락·추가·중복")
        if set(actual_ids) != set(expected_ids):
            # A cut log head can drop early records; that is missing evidence,
            # not proof that the run skipped a check. Extra IDs still fail.
            if (set(actual_ids) < set(expected_ids)
                    and any(line.startswith(TRUNCATED) for line in log.splitlines())):
                return judgment("REVIEW", "시험 로그 앞부분이 잘려 일부 관찰 기록을 확인할 수 없음; 누락으로 판정하지 않음")
            return judgment("FAIL", "TC·계획·실제 관찰의 필수 검사 번호 누락·추가·중복")
        # Compiler emits anchored assertions after their actions, then terminal ones.
        anchors = [a["action_id"] for a in plan["actions"] if a["phase"] != "RESTORE"] + [None]
        ordered = [a for anchor in anchors for a in assertions if a.get("after_action_id") == anchor]
        if len(ordered) != len(assertions):
            return judgment("FAIL", "검사와 조작 시점 연결이 계획에서 누락됨")
        matched = []
        for expected, event in zip(ordered, events):
            if (any(event.get(k) != v for k, v in identity.items())
                    or not equal(event.get("target_device_id"), plan["target_device_id"])
                    or not equal(event["assertion"], expected)):
                return judgment("FAIL", "계획과 기록의 대상·검사·기대값·연결 시점·순서 불일치")
            observed_match = matches(expected, event["actual"])
            if type(event.get("matched")) is not bool or event["matched"] != observed_match:
                return judgment("FAIL", "관찰값과 기록된 일치 여부가 다름")
            matched.append(observed_match)
        if outcome not in {"PASS", "PRODUCT_MISMATCH_CANDIDATE"}:
            return judgment("REVIEW", "본시험 관찰은 있으나 준비·복원·실행 중단 결과는 별도 검토 필요")
        if (outcome == "PASS") != all(matched):
            return judgment("FAIL", "실제 관찰의 기대값 일치 여부와 시험 결과가 다름")
        if sink is not None:
            sink[:] = events
        return judgment("PASS", "TC 검사 번호·계획·기록된 관찰값·시험 결과 일치. 제품 불일치의 정확한 검출도 포함; 코드 구현 품질·복원 전체 평가는 아님")
    except (ValueError, KeyError, TypeError, AttributeError):
        return judgment("REVIEW", "관찰 형식 또는 비교 방식이 지원되지 않아 독립 대조 보류")


def grade_run(run, observations=None):
    """Verify the saved handoff chain before interpreting assertion records.

    ``observations`` (optional dict) receives each TC's verified records, the
    final execution's when it exists, otherwise the candidate trial's.
    """
    evidence = []

    def verified(base, name, expected):
        path = child(base, name)
        if any(p.is_symlink() for p in [base / name, *(base / name).parents] if p.is_relative_to(run)):
            raise ValueError("Linked evidence")
        if path.stat().st_size > 2_000_000 or not expected or digest(path) != expected:
            raise ValueError("Evidence is missing, oversized or unverified")
        evidence.append(dict(file=path.relative_to(run).as_posix(), sha256=expected))
        return path

    try:
        run = run.resolve()
        design_path = run / "agent2_test_design.json"
        design = read(design_path)
        cases = design["test_cases"]
        if not cases:
            # No product observations can establish an absent/new-plan branch.
            return judgment("REVIEW", "신규 TC가 없어 이 실행 관찰 항목은 확인하지 않음; 재사용·제외 분기는 별도 근거로 판단")
        results = []
        for case in cases:
            try:
                execution_manifest = run / "validation_manifest.json"
                links = read(execution_manifest) if execution_manifest.is_file() else {}
                link = {}
                if "source_agent3_artifacts" in links:
                    if links.get("run_id") != run.name or links.get("stage") != "VALIDATION_EXECUTION":
                        raise ValueError("Execution manifest identity mismatch")
                    candidates = [item for item in links["source_agent3_artifacts"]
                                  if item.get("tc_id") == case["tc_id"]]
                    if len(candidates) != 1:
                        raise ValueError("Missing or ambiguous candidate reference")
                    link = candidates[0]
                    manifest_path = verified(run, link["agent3_manifest_file"], link.get("agent3_manifest_sha256"))
                    if manifest_path.name != "agent3_manifest.json":
                        raise ValueError("Unsupported candidate manifest reference")
                    folder = manifest_path.parent
                else:
                    # Older candidate-only records have no execution manifest.
                    folder = child(run / "agent3_candidates", case["tc_id"])
                    manifest_path = folder / "agent3_manifest.json"
                manifest = read(manifest_path)
                if manifest.get("run_id") != run.name or manifest.get("tc_id") != case["tc_id"]:
                    raise ValueError("Manifest target mismatch")
                verified(run, "agent2_test_design.json", manifest.get("source_agent2_design_sha256"))
                verified(run, "agent2_manifest.json", manifest.get("source_agent2_manifest_sha256"))
                plan = read(verified(folder, "agent3_automation_plan.json", manifest.get("automation_plan_sha256")))
                trial = read(verified(folder, manifest["trial_file"], manifest.get("trial_sha256")))
                verified(folder / "candidates", manifest["candidate_file"], manifest.get("candidate_sha256"))
                name = trial["stdout_file"]
                expected = trial["evidence_sha256"][name]
                if manifest.get("trial_evidence_sha256", {}).get(name) != expected:
                    raise ValueError("Conflicting log references")
                path = verified(folder / "evidence" / case["tc_id"], name, expected)
                trial_events, final_events = [], None
                results.append(compare(case, plan, path.read_text(encoding="utf-8"), run_id=run.name,
                                       outcome=trial["outcome"], sink=trial_events))
                # Candidate trial and final execution can have different logs.
                # Keep confirmed earlier violations, but never substitute an old
                # passing trial for missing or inconsistent final observations.
                if (run / "validation_execution.json").is_file() or links.get("validation_execution_sha256"):
                    execution = read(verified(run, "validation_execution.json", links.get("validation_execution_sha256")))
                    if execution.get("run_id") != run.name or execution.get("stage") != "VALIDATION_EXECUTION":
                        raise ValueError("Final execution identity mismatch")
                    rows = execution.get("candidate_results") or [execution.get("candidate_result")]
                    selected = [row for row in rows if row and row.get("test_id") == case["tc_id"]]
                    if len(selected) != 1 or selected[0].get("source") != "NEW_AUTOMATION_CANDIDATE":
                        raise ValueError("Missing or ambiguous final candidate")
                    row = selected[0]
                    code_hash = row.get("test_sha256")
                    if "validation_candidate_sha256" in link and link["validation_candidate_sha256"] != code_hash:
                        raise ValueError("Final candidate code reference mismatch")
                    verified(run, row["test_file"], code_hash)
                    path = verified(run, row["stdout_file"], row["evidence_sha256"].get(row["stdout_file"]))
                    outcome = row["source_outcome"]
                    status = {"PASS": "PASSED", "PRODUCT_MISMATCH_CANDIDATE": "ASSERTION_FAILED"}.get(outcome)
                    if status and row.get("status") != status:
                        raise ValueError("Conflicting final execution status")
                    final_events = []
                    results.append(compare(case, plan, path.read_text(encoding="utf-8"), run_id=run.name,
                                           outcome=outcome, sink=final_events))
                if observations is not None:
                    observations[case["tc_id"]] = trial_events if final_events is None else final_events
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                results.append(judgment("REVIEW", "후보의 연결 자료가 부족하거나 해시 불일치; 다른 후보의 확인된 위반은 보존"))
        verdict = "FAIL" if any(r["verdict"] == "FAIL" for r in results) else "REVIEW" if any(r["verdict"] == "REVIEW" for r in results) else "PASS"
        return judgment(verdict, " / ".join(dict.fromkeys(r["reason"] for r in results)), evidence)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return judgment("REVIEW", "동일 실행의 TC·계획·시험·관찰 기록 연결을 확인할 자료 부족 또는 해시 불일치", evidence)
