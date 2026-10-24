"""Claude judge (2026-10-24): same packet and validation as the OpenAI judge, no live calls here."""
import json
from types import SimpleNamespace

import pytest

from eval_harness import claude_judge, judges, openai_judge
from eval_harness.openai_judge import ReferencedResponse

from test_openai_judge import packet, reference_judgment

MODEL = "claude-sonnet-5-5"


class FakeClaude:
    def __init__(self, value=None, stop_reason="end_turn", error=None):
        self.value, self.stop_reason, self.error = value, stop_reason, error
        self.calls = []
        self.messages = SimpleNamespace(parse=self.parse)

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        parsed = None if self.value is None else ReferencedResponse.model_validate(self.value)
        return SimpleNamespace(id="msg-test", _request_id="req-test", stop_reason=self.stop_reason,
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
    assert claude["destination"] == "https://api.anthropic.com/v1/messages" and claude["maximum_calls"] == 1
    assert request["model"] == MODEL and request["output_config"] == {"effort": "medium"}
    assert request["system"] == [{"type": "text", "text": openai_judge.SYSTEM, "cache_control": {"type": "ephemeral"}}]
    # Identical evidence payload, so the two judges are compared on the same input.
    assert request["messages"][0]["content"] == openai["request"]["input"][1]["content"]
    assert claude["response_schema"] == openai["response_schema"]
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
    assert client.calls[0]["output_format"] is ReferencedResponse and len(client.calls) == 1


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
