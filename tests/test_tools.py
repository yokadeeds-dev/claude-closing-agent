import json

import pytest

from closing_agent.tools import TOOLS, DealTools, ToolError


def run(services, deal_id, name, **args):
    return json.loads(DealTools(services, deal_id).run(name, args))


def test_tool_schemas_are_strict_compatible():
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        assert "deal_id" not in schema["properties"]  # deal scope comes from the session, never the model


def test_summary_contains_order_price_and_open_items(services):
    s = run(services, "D-1002", "get_deal_summary")
    assert s["order"] == {"sku": "CRM-STD", "quantity": 12, "term_months": 12, "extra_discount_pct": 0}
    assert s["price"]["total_net"] == "3571.20"  # 29 * 0.95 * 0.90 = 24.80 x 12 seats x 12 mo
    assert not s["contract"]["complete"]


def test_closed_won_refused_while_contract_incomplete(services):
    r = run(services, "D-1002", "mark_closed_won")
    assert r["closed"] is False and r["open_items"]
    assert services.crm.get_deal("D-1002")["stage"] == "contract_sent"


def test_closed_won_when_audit_passes(services):
    assert run(services, "D-1001", "mark_closed_won") == {"closed": True}
    assert services.crm.get_deal("D-1001")["stage"] == "closed_won"


def test_discount_within_policy_is_applied(services):
    r = run(services, "D-1002", "request_extra_discount", extra_discount_pct=3, reason="multi-site")
    assert r["status"] == "approved" and r["price"]["extra_discount_pct"] == "3"
    assert services.crm.get_deal("D-1002")["extra_discount_pct"] == 3


def test_discount_above_policy_needs_human(services):
    r = run(services, "D-1002", "request_extra_discount", extra_discount_pct=12, reason="competitor")
    assert r["status"] == "pending_approval"
    assert "extra_discount_pct" not in services.crm.get_deal("D-1002")
    [task] = services.crm.tasks("D-1002")
    assert task["title"].startswith("Discount approval: 12%")


def test_select_option_updates_deal_and_voids_discount(services):
    run(services, "D-1004", "request_extra_discount", extra_discount_pct=2, reason="x")
    r = run(services, "D-1004", "select_option", sku="CRM-STD", quantity=10, term_months=24)
    deal = services.crm.get_deal("D-1004")
    assert r["recorded"] and deal["stage"] == "option_selected"
    assert (deal["quantity"], deal["term_months"], deal["extra_discount_pct"]) == (10, 24, 0)


def test_bad_input_becomes_tool_error(services):
    with pytest.raises(ToolError, match="Unsupported term"):
        DealTools(services, "D-1002").run("select_option", {"sku": "CRM-STD", "quantity": 5, "term_months": 7})
    with pytest.raises(ToolError, match="Unknown tool"):
        DealTools(services, "D-1002").run("delete_everything", {})
