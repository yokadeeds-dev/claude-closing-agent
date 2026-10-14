"""Reminder sequence: nudge the buyer until the contract audit passes, then stop.

State lives in SQLite so a restart (or a cron-driven `run_due`) never sends a
reminder twice. Delivery goes through a `Sender`; the default `OutboxSender`
appends to a JSONL file - swap in SMTP / the CRM's e-mail API in production.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .audit import AuditResult
from .crm import CRM


class Sender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class OutboxSender:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def send(self, to: str, subject: str, body: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _iso(datetime.now(UTC)), "to": to, "subject": subject,
                                 "body": body}) + "\n")


@dataclass
class NudgeConfig:
    schedule_days: list[int]
    min_hours_between: int
    escalate_after: int


SCHEMA = """
CREATE TABLE IF NOT EXISTS sequences (
    deal_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL            -- active | done | escalated
);
CREATE TABLE IF NOT EXISTS nudges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    reason TEXT NOT NULL,           -- schedule | agent
    subject TEXT NOT NULL,
    missing TEXT NOT NULL
);
"""


class NudgeService:
    def __init__(self, db_path: str | Path, config: NudgeConfig, messages: dict, crm: CRM,
                 audit_deal: Callable[[str], AuditResult], sender: Sender,
                 link_for: Callable[[str], str] = lambda deal_id: "#"):
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.config, self.messages, self.crm = config, messages, crm
        self.audit_deal, self.sender, self.link_for = audit_deal, sender, link_for

    def close(self) -> None:
        self.db.close()

    # ---------- queries ----------

    def status(self, deal_id: str) -> dict:
        row = self.db.execute("SELECT started_at, status FROM sequences WHERE deal_id=?", (deal_id,)).fetchone()
        sent = self.db.execute(
            "SELECT attempt, sent_at, reason, subject, missing FROM nudges WHERE deal_id=? ORDER BY id",
            (deal_id,)).fetchall()
        return {
            "sequence": {"started_at": row[0], "status": row[1]} if row else None,
            "sent": [{"attempt": a, "sent_at": s, "reason": r, "subject": subj, "missing": json.loads(m)}
                     for a, s, r, subj, m in sent],
        }

    def _attempts(self, deal_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM nudges WHERE deal_id=?", (deal_id,)).fetchone()[0]

    def _last_sent(self, deal_id: str) -> datetime | None:
        row = self.db.execute("SELECT MAX(sent_at) FROM nudges WHERE deal_id=?", (deal_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    # ---------- actions ----------

    def start(self, deal_id: str, now: datetime) -> None:
        self.db.execute("INSERT OR IGNORE INTO sequences VALUES (?, ?, 'active')", (deal_id, _iso(now)))
        self.db.commit()

    def send_now(self, deal_id: str, now: datetime) -> dict:
        """Agent-triggered reminder, rate-limited so the model cannot spam the buyer."""
        audit = self.audit_deal(deal_id)
        if audit.complete:
            return {"sent": False, "reason": "contract is already complete"}
        last = self._last_sent(deal_id)
        gap = timedelta(hours=self.config.min_hours_between)
        if last and now - last < gap:
            return {"sent": False, "reason": f"last reminder was sent at {_iso(last)}; "
                                             f"next one allowed after {_iso(last + gap)}"}
        self.start(deal_id, now)
        return {"sent": True, **self._send(deal_id, audit, now, "agent")}

    def run_due(self, now: datetime) -> list[dict]:
        """Process every active sequence. Call from cron / a scheduler, e.g. hourly."""
        events = []
        rows = self.db.execute("SELECT deal_id, started_at FROM sequences WHERE status='active'").fetchall()
        for deal_id, started_at in rows:
            audit = self.audit_deal(deal_id)
            if audit.complete:
                self._set_status(deal_id, "done")
                self.crm.add_note(deal_id, "Contract audit passed - reminder sequence stopped.")
                events.append({"deal_id": deal_id, "event": "completed"})
                continue
            attempts = self._attempts(deal_id)
            if attempts >= self.config.escalate_after:
                self._escalate(deal_id, audit, attempts)
                events.append({"deal_id": deal_id, "event": "escalated"})
                continue
            if attempts >= len(self.config.schedule_days):
                continue
            due = datetime.fromisoformat(started_at) + timedelta(days=self.config.schedule_days[attempts])
            last = self._last_sent(deal_id)
            too_soon = last and now - last < timedelta(hours=self.config.min_hours_between)
            if now >= due and not too_soon:
                events.append({"deal_id": deal_id, "event": "sent", **self._send(deal_id, audit, now, "schedule")})
        return events

    # ---------- internals ----------

    def _send(self, deal_id: str, audit: AuditResult, now: datetime, reason: str) -> dict:
        deal = self.crm.get_deal(deal_id)
        attempt = self._attempts(deal_id) + 1
        templates = self.messages["reminders"]
        tpl = templates[min(attempt, len(templates)) - 1]
        missing = audit.summary_lines()
        fields = {"contact": deal["contact"].split()[0], "company": deal["company"],
                  "missing_list": "\n".join(f"- {line}" for line in missing),
                  "link": self.link_for(deal_id)}
        subject, body = tpl["subject"].format(**fields), tpl["body"].format(**fields)
        self.sender.send(deal["email"], subject, body)
        self.db.execute("INSERT INTO nudges (deal_id, attempt, sent_at, reason, subject, missing) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (deal_id, attempt, _iso(now), reason, subject, json.dumps(missing)))
        self.db.commit()
        self.crm.add_note(deal_id, f"Reminder #{attempt} sent ({reason}): {len(missing)} open item(s).")
        return {"attempt": attempt, "to": deal["email"], "subject": subject, "open_items": missing}

    def _escalate(self, deal_id: str, audit: AuditResult, attempts: int) -> None:
        deal = self.crm.get_deal(deal_id)
        body = self.messages["escalation_task"].format(
            company=deal["company"], contact=deal["contact"], attempts=attempts,
            missing_list="\n".join(f"- {line}" for line in audit.summary_lines()))
        self.crm.create_task(deal_id, f"Contract stuck: {deal['company']}", body)
        self.crm.update_deal(deal_id, stage="needs_human")
        self._set_status(deal_id, "escalated")

    def _set_status(self, deal_id: str, status: str) -> None:
        self.db.execute("UPDATE sequences SET status=? WHERE deal_id=?", (status, deal_id))
        self.db.commit()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")
