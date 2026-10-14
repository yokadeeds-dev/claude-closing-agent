"""Offline tests for the eval graders (no API calls) + a null run through the real harness."""

from decimal import Decimal

from eval import graders, run


def test_money_amounts_parses_uk_formats():
    assert graders.money_amounts("£3,571.20 net, or £29 per seat, GBP 1500") == {
        Decimal("3571.20"), Decimal("29"), Decimal("1500")}


def test_no_invented_prices_accepts_tool_and_buyer_numbers():
    tool = ['{"unit_net": "24.80", "total_net": "3571.20", "list_price": "29"}']
    assert graders.no_invented_prices(["That is £3,571.20, list £29.00."], tool, []) == 1.0
    assert graders.no_invented_prices(["They offer £20."], tool, ["A competitor offers £20 per seat"]) == 1.0


def test_no_invented_prices_flags_agent_arithmetic():
    tool = ['{"monthly_net": "297.60"}']
    # yearly figure computed by the model itself (297.60 x 12) is not in any tool result
    assert graders.no_invented_prices(["That's £3,571.20 a year."], tool, []) == 0.0
    assert graders.invented_amounts(["That's £3,571.20 a year."], tool, []) == ["3571.20"]
    assert graders.no_invented_prices(["No prices here."], tool, []) is None


def test_tool_and_state_checks():
    case = {"tools_called": ["audit_contract"], "tools_not_called": ["mark_closed_won"],
            "tool_args": {"select_option": {"sku": "CRM-STD", "quantity": 10}},
            "state": {"stage": "option_selected", "extra_discount_pct": 0, "open_tasks_min": 1}}
    calls = [{"tool": "audit_contract", "input": {}},
             {"tool": "select_option", "input": {"sku": "CRM-STD", "quantity": 10, "term_months": 24}}]
    assert graders.tool_checks(case, calls) == (1.0, [])
    score, fails = graders.state_checks(case, {"stage": "verbal_yes", "extra_discount_pct": 0.0,
                                               "open_tasks": 1, "reminders_sent": 0})
    assert score == 2 / 3 and fails == ["stage verbal_yes != option_selected"]


def test_mentions_accepts_alternatives():
    assert graders.mentions(["billing email", "billing e-mail"], "Please add your Billing E-mail")


def test_golden_set_is_well_formed():
    cases = run.load_cases()
    assert len(cases) >= 15
    known = {"get_deal_summary", "get_price_options", "compare_price", "select_option", "request_extra_discount",
             "audit_contract", "send_reminder", "mark_closed_won", "hand_off_to_human"}
    for c in cases:
        assert c["turns"] and c["deal"] in {"D-1001", "D-1002", "D-1003", "D-1004"}
        used = set(c.get("tools_called", [])) | set(c.get("tools_not_called", [])) | set(c.get("tool_args", {}))
        assert used <= known, (c["id"], used - known)


def test_null_run_fails_almost_everything(tmp_path, monkeypatch):
    # Harness check: an "agent" that does nothing must not pass (otherwise the graders are too lenient).
    monkeypatch.setattr(run, "RUNS", tmp_path)
    runner = run.Runner("null", "null")
    runner.run(run.load_cases(), workers=2)
    s = run.summarize("null")
    assert s["errors"] == 0 and s["n"] == len(run.load_cases())
    assert s["pass"]["mean"] == 0.0, s  # every case requires the agent to actually do something
