import json
import shutil
from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def outbox(services):
    p = services.settings.path("outbox")
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_schedule_sends_on_due_days_only(services):
    n = services.nudges
    n.start("D-1002", T0)
    assert [e["event"] for e in n.run_due(T0)] == ["sent"]          # day 0
    assert n.run_due(T0 + timedelta(days=1)) == []                   # day 2 not yet due
    assert [e["event"] for e in n.run_due(T0 + timedelta(days=2))] == ["sent"]
    mails = outbox(services)
    assert len(mails) == 2 and mails[0]["to"] == "sam.patel@wearside.example"
    assert "Customer signature is missing" in mails[0]["body"]
    assert "/widget?deal=D-1002&t=" in mails[0]["body"]


def test_sequence_stops_when_contract_complete(services):
    n = services.nudges
    n.start("D-1002", T0)
    n.run_due(T0)
    # buyer completes the contract: point the deal at a complete PDF
    root = services.settings.root
    shutil.copy(root / "data/contracts/D-1001_northbay.pdf", root / "data/contracts/fixed.pdf")
    services.crm.update_deal("D-1002", contract="data/contracts/fixed.pdf", sku="CRM-PRO",
                             quantity=30, term_months=24)
    assert [e["event"] for e in n.run_due(T0 + timedelta(days=2))] == ["completed"]
    assert n.status("D-1002")["sequence"]["status"] == "done"
    assert n.run_due(T0 + timedelta(days=30)) == []
    assert len(outbox(services)) == 1


def test_escalates_to_human_after_max_reminders(services):
    n = services.nudges
    n.start("D-1002", T0)
    for day in (0, 2, 5, 9):
        n.run_due(T0 + timedelta(days=day))
    assert [e["event"] for e in n.run_due(T0 + timedelta(days=10))] == ["escalated"]
    assert services.crm.get_deal("D-1002")["stage"] == "needs_human"
    [task] = services.crm.tasks("D-1002")
    assert "after 4 reminders" in task["body"]


def test_agent_triggered_reminder_is_rate_limited(services):
    n = services.nudges
    assert n.send_now("D-1002", T0)["sent"] is True
    second = n.send_now("D-1002", T0 + timedelta(hours=3))
    assert second["sent"] is False and "next one allowed" in second["reason"]
    assert n.send_now("D-1002", T0 + timedelta(hours=25))["sent"] is True


def test_no_reminder_for_complete_contract(services):
    assert services.nudges.send_now("D-1001", T0) == {"sent": False, "reason": "contract is already complete"}
    assert outbox(services) == []
