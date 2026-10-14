import json
from types import SimpleNamespace

from conftest import FakeClient, response, text_block, tool_block
from fastapi.testclient import TestClient

from closing_agent.agent import HANDOFF_TEXT, ClosingAgent
from closing_agent.app import create_app


def test_tool_loop_runs_tools_and_returns_text(services):
    fake = FakeClient([
        response([tool_block("get_deal_summary", {}, "tu_1"), tool_block("audit_contract", {}, "tu_2")],
                 "tool_use"),
        response([text_block("You still need to sign page 3.")], "end_turn"),
    ])
    agent = ClosingAgent(services, client=fake)
    reply = agent.chat("D-1002", "What is left to do?")

    assert reply.text == "You still need to sign page 3."
    assert [c["tool"] for c in reply.tool_calls] == ["get_deal_summary", "audit_contract"]
    second = fake.requests[1]
    # both tool results go back in ONE user message, matched by id
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["tu_1", "tu_2"]
    assert "Page 3: Customer signature is missing" in json.loads(results[1]["content"])["open_items"]
    # request shape: model from settings, refusal fallback enabled, strict tools
    assert second["model"] == "claude-opus-5-5"
    assert second["fallbacks"] == "default" and second["betas"] == ["server-side-fallback-2026-07-01"]
    assert "{catalog}" not in second["system"] and "CRM-PRO" in second["system"]


def test_history_is_append_only_across_turns(services):
    fake = FakeClient([response([text_block("Hi")], "end_turn"), response([text_block("Sure")], "end_turn")])
    agent = ClosingAgent(services, client=fake)
    agent.chat("D-1002", "hello")
    agent.chat("D-1002", "and now?")
    first, second = fake.requests[0]["messages"], fake.requests[1]["messages"]
    assert second[: len(first)] == first  # earlier turns are never rewritten
    assert len(agent.history("D-1002")) == 4


def test_tool_error_is_returned_not_raised(services):
    fake = FakeClient([
        response([tool_block("select_option", {"sku": "CRM-STD", "quantity": 5, "term_months": 7}, "tu_1")],
                 "tool_use"),
        response([text_block("That term is not available.")], "end_turn"),
    ])
    reply = ClosingAgent(services, client=fake).chat("D-1002", "5 seats for 7 months")
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "Unsupported term" in result["content"]
    assert reply.tool_calls[0]["is_error"] is True


def test_refusal_hands_off_and_keeps_history_clean(services):
    fake = FakeClient([response([], "refusal", stop_details=SimpleNamespace(category="general_harms"))])
    agent = ClosingAgent(services, client=fake)
    reply = agent.chat("D-1002", "something odd")
    assert reply.text == HANDOFF_TEXT and reply.stop_reason == "refusal"
    assert agent.history("D-1002") == []
    assert services.crm.tasks("D-1002")[0]["title"] == "Closing agent declined a message"


# ---------- HTTP API ----------

def client_for(services, fake=None):
    return TestClient(create_app(services, ClosingAgent(services, client=fake) if fake else None))


def test_buyer_link_requires_valid_token(services):
    c = client_for(services)
    assert c.get("/api/deals/D-1002?t=wrong").status_code == 403
    assert c.get("/api/deals/D-9999?t=x").status_code == 404
    r = c.get(f"/api/deals/D-1002?t={services.token('D-1002')}")
    assert r.status_code == 200 and r.json()["contract"]["complete"] is False


def test_token_for_one_deal_does_not_open_another(services):
    c = client_for(services)
    assert c.get(f"/api/deals/D-1001?t={services.token('D-1002')}").status_code == 403


def test_chat_endpoint(services):
    fake = FakeClient([response([text_block("Hello Sam!")], "end_turn")])
    c = client_for(services, fake)
    r = c.post(f"/api/deals/D-1002/chat?t={services.token('D-1002')}", json={"message": "hi"})
    assert r.status_code == 200 and r.json()["reply"] == "Hello Sam!"


def test_upload_contract_reaudits_and_starts_reminders(services):
    c = client_for(services)
    pdf = (services.settings.root / "data/contracts/D-1002_wearside.pdf").read_bytes()
    r = c.post(f"/api/deals/D-1002/contract?t={services.token('D-1002')}",
               files={"file": ("../../evil name.pdf", pdf, "application/pdf")})
    assert r.status_code == 200 and r.json()["complete"] is False
    stored = services.crm.get_deal("D-1002")["contract"]
    assert stored.startswith("data") and "evil" not in stored
    assert services.nudges.status("D-1002")["sequence"]["status"] == "active"
    bad = c.post(f"/api/deals/D-1002/contract?t={services.token('D-1002')}",
                 files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert bad.status_code == 415


def test_admin_endpoints_need_key(services):
    c = client_for(services)
    assert c.get("/api/admin/deals/D-1002").status_code == 403
    r = c.get("/api/admin/deals/D-1002", headers={"X-Admin-Key": "admin-test"})
    assert r.status_code == 200 and len(r.json()["audit"]["findings"]) == 4


def test_chat_without_credentials_returns_503(services):
    class NoAuth(FakeClient):
        def _create(self, **kwargs):
            raise TypeError('"Could not resolve authentication method. Expected one of api_key..."')

    c = client_for(services, NoAuth([]))
    r = c.post(f"/api/deals/D-1002/chat?t={services.token('D-1002')}", json={"message": "hi"})
    assert r.status_code == 503


# ---------- regressions from code review ----------

def test_refusal_after_tool_round_rolls_back_whole_turn(services):
    fake = FakeClient([
        response([tool_block("get_deal_summary", {}, "tu_1")], "tool_use"),
        response([], "refusal", stop_details=SimpleNamespace(category=None)),
        response([text_block("Fine")], "end_turn"),
    ])
    agent = ClosingAgent(services, client=fake)
    assert agent.chat("D-1002", "odd request").stop_reason == "refusal"
    assert agent.history("D-1002") == []  # no orphaned tool_use left behind
    agent.chat("D-1002", "normal question")
    assert [m["role"] for m in fake.requests[2]["messages"]] == ["user"]


def test_api_error_mid_turn_rolls_back_history(services):
    class Boom(FakeClient):
        def _create(self, **kwargs):
            if self.requests:
                raise RuntimeError("network down")
            return super()._create(**kwargs)

    fake = Boom([response([tool_block("audit_contract", {}, "tu_1")], "tool_use")])
    agent = ClosingAgent(services, client=fake)
    try:
        agent.chat("D-1002", "check my contract")
    except RuntimeError:
        pass
    assert agent.history("D-1002") == []


def test_corrupt_contract_becomes_tool_error_not_crash(services):
    (services.settings.root / "data/contracts/D-1002_wearside.pdf").write_bytes(b"%PDF-1.4 truncated")
    fake = FakeClient([
        response([tool_block("audit_contract", {}, "tu_1")], "tool_use"),
        response([text_block("I'll pass this to the team.")], "end_turn"),
    ])
    reply = ClosingAgent(services, client=fake).chat("D-1002", "is my contract ok?")
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "temporarily unavailable" in result["content"]
    assert reply.text == "I'll pass this to the team."
