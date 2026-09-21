import json
from types import SimpleNamespace

import pytest

from eval_harness.openai_judge import JudgePacket, JudgeResponse, evaluate, preview, validate_judgment


def packet():
    return JudgePacket(case_id="SELF",trial_id="T001",agent_id="A2",target_version="synthetic",
        sources={"request":{"text":"Set temperature to 18 degrees."},
                 "tc":{"steps":["Select device","Set 18 degrees","Apply"],"expected":"Device card shows 18 degrees."}},
        rubric=[dict(criterion_id="DETAIL",objective="구체적 관찰",pass_condition="관찰 위치와 값이 명시됨")],
        code_checks=[dict(rule="VALUE",status="PASS",reason="합성 예시")])


def judgment():
    return dict(judgments=[dict(criterion_id="DETAIL",verdict="PASS",reason="장비 카드와 온도를 명시함",
                               evidence=[dict(source_id="tc",path=["expected"],quote="Device card shows 18 degrees.")])],limitations=["합성 응답"])


class Fake:
    def __init__(self,value=None,error=None):
        self.value=value
        self.error=error
        self.calls=[]
        self.responses=SimpleNamespace(parse=self.parse)

    def parse(self,**kwargs):
        self.calls.append(kwargs)
        if self.error: raise self.error
        return SimpleNamespace(id="resp-test",_request_id="req-test",status="completed",
                               output_parsed=self.value,usage=SimpleNamespace(input_tokens=10,output_tokens=20,total_tokens=30))


def execute(tmp_path,p=None,value=None,error=None):
    p=p or packet()
    client=Fake(value=judgment() if value is None else value,error=error)
    out=evaluate(p,"configured-model",preview(p,"configured-model")["approval_sha256"],tmp_path,client_factory=lambda:client)
    return json.loads((out/"result.json").read_text(encoding="utf-8")),client,out


def test_preview_no_key_no_client_and_no_code_label_leakage(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY",raising=False)
    info=preview(packet(),"configured-model")
    assert info["maximum_calls"]==1
    assert info["max_retries"]==0
    assert not info["request"]["store"]
    payload=json.loads(info["request"]["input"][1]["content"])
    assert "code_checks" not in payload
    assert "previous_response_id" not in info["request"]
    assert "tools" not in info["request"]


@pytest.mark.parametrize("change",["model","source","rubric","code_check"])
def test_approval_bound_to_request_and_result_context(tmp_path,change):
    p=packet()
    old=preview(p,"configured-model")["approval_sha256"]
    model="configured-model"
    if change=="model": model="different-model"
    if change=="source": p.sources["request"]["text"]="changed"
    if change=="rubric": p.rubric[0].pass_condition="changed"
    if change=="code_check": p.code_checks[0].status="FAIL"
    with pytest.raises(ValueError):
        evaluate(p,model,old,tmp_path,client_factory=lambda:pytest.fail("Client must not be constructed"))


@pytest.mark.parametrize("value",["sk-abcdefghijklmnopqrstuv","C:/Users/private/file","Bearer abc123","<html>full source</html>","/home/private/file"])
def test_sensitive_sources_rejected(value):
    p=packet()
    p.sources["request"]["text"]=value
    with pytest.raises(ValueError): preview(p,"configured-model")


def test_valid_judge_response_stays_advisory(tmp_path):
    result,client,out=execute(tmp_path)
    assert len(client.calls)==1
    assert client.calls[0]["text_format"] is JudgeResponse
    assert result["structured_response_valid"]
    assert result["status"]=="COMPLETED"
    assert result["assessment"]=="REVIEW"
    assert result["mode"]=="TEST_DOUBLE" and result["api_request_attempts"]==0
    assert result["human_review_required"] and not result["performance_claim_eligible"]
    assert result["usage"]["total_tokens"]==30


def test_ai_pass_cannot_override_code_failure(tmp_path):
    p=packet()
    p.code_checks[0].status="FAIL"
    result,_,_=execute(tmp_path,p)
    assert result["assessment"]=="ISSUES_FOUND"
    assert result["code_checks"][0]["status"]=="FAIL"


@pytest.mark.parametrize("mutation",["missing","extra","duplicate","quote","path","source"])
def test_unreliable_judgments_rejected(tmp_path,mutation):
    data=judgment()
    if mutation=="missing": data["judgments"]=[]
    elif mutation=="extra": data["judgments"][0]["criterion_id"]="UNKNOWN"
    elif mutation=="duplicate": data["judgments"]*=2
    elif mutation=="quote": data["judgments"][0]["evidence"][0]["quote"]="invented"
    elif mutation=="path": data["judgments"][0]["evidence"][0]["path"]=["missing"]
    elif mutation=="source": data["judgments"][0]["evidence"][0]["source_id"]="missing"
    result,client,_=execute(tmp_path,value=data)
    assert result["status"]=="ERROR"
    assert not result["structured_response_valid"]
    assert len(client.calls)==1


def test_timeout_not_retried_and_secret_exception_not_saved(tmp_path):
    result,client,out=execute(tmp_path,error=TimeoutError("secret-value"))
    assert len(client.calls)==1
    assert result["server_completion"]=="UNKNOWN"
    assert result["error_type"]=="TimeoutError"
    assert "secret-value" not in (out/"result.json").read_text(encoding="utf-8")


def test_real_client_constructor_fixed_origin_no_retries(monkeypatch):
    import sys
    from eval_harness.openai_judge import new_client
    seen={}
    def constructor(**kwargs): seen.update(kwargs); return object()
    monkeypatch.setenv("OPENAI_API_KEY","not-a-real-key")
    monkeypatch.setenv("OPENAI_BASE_URL","https://untrusted.invalid")
    monkeypatch.setitem(sys.modules,"openai",SimpleNamespace(OpenAI=constructor))
    new_client()
    assert seen["base_url"]=="https://api.openai.com/v1"
    assert seen["max_retries"]==0 and seen["timeout"]==120


def test_cli_preview_does_not_call_network(tmp_path,capsys,monkeypatch):
    from eval_harness.cli import main
    import eval_harness.openai_judge as module
    file=tmp_path/"packet.json"
    file.write_text(packet().model_dump_json(),encoding="utf-8")
    monkeypatch.setattr(module,"new_client",lambda:pytest.fail("No live call allowed"))
    assert main(["judge-openai","--packet",str(file),"--model","configured-model"])==0
    assert "approval_sha256" in capsys.readouterr().out


@pytest.mark.parametrize("http_status",[200,429,500])
def test_real_sdk_with_mock_transport_only_one_request(tmp_path,http_status):
    openai=pytest.importorskip("openai")
    import httpx
    requests=[]
    def handler(request):
        requests.append(request)
        if http_status!=200:
            return httpx.Response(http_status,json={"error":{"message":"synthetic","type":"server_error"}})
        return httpx.Response(200,headers={"x-request-id":"req-local-test"},json={
            "id":"resp-local-test","object":"response","created_at":1,"status":"completed","model":"configured-model",
            "output":[{"id":"msg-local-test","type":"message","status":"completed","role":"assistant",
                       "content":[{"type":"output_text","text":json.dumps(judgment()),"annotations":[]}]}],
            "usage":{"input_tokens":10,"output_tokens":20,"total_tokens":30}})
    with openai.OpenAI(api_key="local-test-only",max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        p=packet()
        out=evaluate(p,"configured-model",preview(p,"configured-model")["approval_sha256"],tmp_path,client_factory=lambda:client)
    result=json.loads((out/"result.json").read_text(encoding="utf-8"))
    assert len(requests)==1
    sent=json.loads(requests[0].content)
    assert sent["text"]["format"]["type"]=="json_schema"
    assert sent["store"] is False
    assert result["status"]==("COMPLETED" if http_status==200 else "ERROR")
    assert result["api_request_attempts"]==0
