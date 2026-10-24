"""Claude judge (2026-10-24): same packet and validation as the OpenAI judge, no live calls here."""
import json
from types import SimpleNamespace

import pytest

from eval_harness import claude_judge, judges, openai_judge
from eval_harness.claude_judge import ClaudeReferencedResponse

from test_openai_judge import packet, reference_judgment

MODEL = "claude-sonnet-5-5"


class FakeClaude:
    def __init__(self, value=None, stop_reason="end_turn", error=None, values=None):
        self.value, self.stop_reason, self.error = value, stop_reason, error
        self.values = list(values or [])  # one value per call, for retry tests
        self.calls = []
        self.messages = SimpleNamespace(stream=self.stream)

    def stream(self, **kwargs):
        message = self.parse(**kwargs)

        class Stream:
            def __enter__(self):
                return SimpleNamespace(get_final_message=lambda: message)

            def __exit__(self, *exc):
                return False
        return Stream()

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if self.values:
            self.value = self.values.pop(0)
        value = None if self.value is None else {**self.value, "judgments": [
            {"score": None, **j} for j in self.value["judgments"]]}
        parsed = None if value is None else ClaudeReferencedResponse.model_validate(value)
        return SimpleNamespace(id=f"msg-test-{len(self.calls)}", _request_id="req-test", stop_reason=self.stop_reason,
                               parsed_output=parsed,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200,
                                                     cache_creation_input_tokens=3000, cache_read_input_tokens=0))


def run(tmp_path, client, p=None):
    p = p or packet()
    info = claude_judge.preview(p, MODEL)
    out = claude_judge.evaluate(p, MODEL, info["approval_sha256"], tmp_path, client_factory=lambda: client)
    return json.loads((out / "result.json").read_text(encoding="utf-8"))


def test_preview_sends_the_same_judge_input_as_openai_with_a_cached_system_prompt():
    p = packet()
    claude = claude_judge.preview(p, MODEL)
    openai = openai_judge.preview(p, "configured-model")
    request = claude["request"]
    assert claude["destination"] == "https://api.anthropic.com/v1/messages" and claude["maximum_calls"] == 2
    assert request["model"] == MODEL and request["output_config"] == {"effort": "high"}
    assert request["system"] == [{"type": "text", "text": openai_judge.SYSTEM, "cache_control": {"type": "ephemeral"}}]
    # Identical evidence payload, so the two judges are compared on the same input.
    assert request["messages"][0]["content"] == openai["request"]["input"][1]["content"]
    # Same fields as the OpenAI schema; only score is required (nullable) for Claude (1.1).
    judgment = claude["response_schema"]["$defs"]["ClaudeReferencedJudgment"]
    assert "score" in judgment["required"]
    assert set(judgment["properties"]) == set(openai["response_schema"]["$defs"]["ReferencedJudgment"]["properties"])
    assert claude["approval_sha256"] != openai["approval_sha256"]


def test_selector_routes_by_model_name():
    p = packet()
    assert judges.preview(p, MODEL)["destination"].startswith("https://api.anthropic.com")
    assert judges.preview(p, "gpt-6.1-sol")["destination"].startswith("https://api.openai.com")
    with pytest.raises(ValueError, match="claude-"):
        claude_judge.preview(p, "gpt-6.1-sol")


def test_valid_response_is_resolved_and_costed(tmp_path):
    client = FakeClaude(value=reference_judgment())
    result = run(tmp_path, client)
    assert result["status"] == "COMPLETED" and result["judge_provider"] == "anthropic"
    assert result["judgments"][0]["evidence"][0]["quote"] == "Device card shows 18 degrees."
    assert result["usage"]["cache_creation_input_tokens"] == 3000
    # 1000*2 + 200*10 + 3000*2*1.25 per million tokens
    assert result["estimated_cost_usd"] == round((2000 + 2000 + 7500) / 1_000_000, 4)
    assert client.calls[0]["output_format"] is ClaudeReferencedResponse and len(client.calls) == 1


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_refusal_or_truncation_is_an_error_not_a_retry(tmp_path, stop_reason):
    client = FakeClaude(value=reference_judgment(), stop_reason=stop_reason)
    result = run(tmp_path, client)
    assert result["status"] == "ERROR" and result["server_completion"] == stop_reason
    assert len(client.calls) == 1 and result["judgments"] == []


def test_unknown_evidence_id_is_rejected(tmp_path):
    value = reference_judgment()
    value["judgments"][0]["evidence_ids"] = ["E999"]
    result = run(tmp_path, FakeClaude(value=value))
    assert result["status"] == "ERROR" and result["validation_error"]["code"] == "UNKNOWN_EVIDENCE_ID"


def test_approval_is_bound_to_model_and_key_is_required(tmp_path, monkeypatch):
    p = packet()
    other = claude_judge.preview(p, "claude-opus-5-5")["approval_sha256"]
    with pytest.raises(ValueError, match="exact preview"):
        claude_judge.evaluate(p, MODEL, other, tmp_path, client_factory=lambda: pytest.fail("no client"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        claude_judge.new_client()


def test_sdk_schema_requires_score():
    """2026-10-24: Claude left score empty on a FAIL when score was optional."""
    from pydantic import TypeAdapter
    from anthropic.lib._parse._transform import transform_schema
    schema = transform_schema(TypeAdapter(ClaudeReferencedResponse).json_schema())
    assert "score" in schema["$defs"]["ClaudeReferencedJudgment"]["required"]


def test_effort_can_be_chosen_in_the_model_name():
    """1.2 (2026-10-24): 'claude-sonnet-5-5:high' sends model claude-sonnet-5-5 at effort high."""
    p = packet()
    high = claude_judge.preview(p, "claude-sonnet-5-5:high")
    assert high["request"]["model"] == "claude-sonnet-5-5" and high["request"]["output_config"] == {"effort": "high"}
    # 1.4: high is the default, so the bare name sends the same request.
    assert high["approval_sha256"] != claude_judge.preview(p, "claude-sonnet-5-5:medium")["approval_sha256"]
    assert claude_judge.preview(p, MODEL)["request"] == high["request"]
    assert judges.preview(p, "claude-sonnet-5-5:high")["destination"].startswith("https://api.anthropic.com")
    with pytest.raises(ValueError, match="effort"):
        claude_judge.preview(p, "claude-sonnet-5-5:fast")
    assert claude_judge.estimated_cost_usd("claude-sonnet-5-5:high", {"input_tokens": 1_000_000}) == 2.0


def test_haiku_long_prompt_rate():
    short = {"input_tokens": 90_000, "output_tokens": 1_000}
    long = {"input_tokens": 110_000, "output_tokens": 1_000}
    assert claude_judge.estimated_cost_usd("claude-haiku-5-5:high", short) == round((90_000 * 0.1 + 1_000 * 0.5) / 1e6, 4)
    assert claude_judge.estimated_cost_usd("claude-haiku-5-5:high", long) == round((110_000 * 0.5 + 1_000 * 2.5) / 1e6, 4)


def test_missing_criteria_are_requested_once_more(tmp_path):
    """1.5 (2026-10-24, IMP-017): Claude answered 1 of 6 criteria with end_turn."""
    full = reference_judgment()
    partial = {**full, "judgments": full["judgments"][:0]}
    client = FakeClaude(values=[partial, full])
    result = run(tmp_path, client)
    out = next(tmp_path.glob("JUDGE-*"))
    assert result["status"] == "COMPLETED" and len(client.calls) == 2
    assert result["sdk_parse_attempts"] == 2 and result["retried_after"][0]["code"] == "CRITERION_SET_MISMATCH"
    # Both calls are billed: usage is summed.
    assert result["usage"]["input_tokens"] == 2000
    assert (out / "received_response_attempt_1.json").is_file() and (out / "received_response.json").is_file()
    assert json.loads((out / "attempt.json").read_text(encoding="utf-8"))["automatic_retries"] == 1


def test_missing_criteria_twice_is_an_error_and_other_errors_are_not_retried(tmp_path):
    full = reference_judgment()
    partial = {**full, "judgments": []}
    client = FakeClaude(values=[partial, partial, full])
    result = run(tmp_path / "a", client)
    assert result["status"] == "ERROR" and len(client.calls) == 2
    assert result["validation_error"]["code"] == "CRITERION_SET_MISMATCH"
    bad = reference_judgment()
    bad["judgments"][0]["evidence_ids"] = ["E999"]
    client = FakeClaude(value=bad)
    assert run(tmp_path / "b", client)["status"] == "ERROR" and len(client.calls) == 1
