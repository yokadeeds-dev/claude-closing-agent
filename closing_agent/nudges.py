"""Reminder sequence: nudge the buyer until the contract audit passes, then stop.

State lives in SQLite so a restart (or a cron-driven `run_due`) never sends a
reminder twice. Delivery goes through a `Sender`; the default `OutboxSender`
appends to a JSONL file - swap in SMTP / the CRM's e-mail API in production.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .audit import AuditResult
from .crm import CRM

log = logging.getLogger(__name__)


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
        # cron (run_due) and the agent (send_now) run in different threads; every
        # check-then-send must be atomic or the buyer can get the same reminder twice
        self._lock = threading.RLock()

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
        """Reminders sent in the current sequence (a restarted sequence counts from zero)."""
        return self.db.execute(
            "SELECT COUNT(*) FROM nudges n JOIN sequences s ON s.deal_id = n.deal_id "
            "WHERE n.deal_id=? AND n.sent_at >= s.started_at", (deal_id,)).fetchone()[0]

    def _status(self, deal_id: str) -> str | None:
        row = self.db.execute("SELECT status FROM sequences WHERE deal_id=?", (deal_id,)).fetchone()
        return row[0] if row else None

    def _last_sent(self, deal_id: str) -> datetime | None:
        row = self.db.execute("SELECT MAX(sent_at) FROM nudges WHERE deal_id=?", (deal_id,)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    # ---------- actions ----------

    def start(self, deal_id: str, now: datetime) -> None:
        """Start a sequence, or restart one that is done/escalated (rep button, new upload)."""
        with self._lock:
            self.db.execute(
                "INSERT INTO sequences VALUES (?, ?, 'active') ON CONFLICT(deal_id) DO UPDATE "
                "SET status='active', started_at=excluded.started_at WHERE sequences.status != 'active'",
                (deal_id, _iso(now)))
            self.db.commit()

    def send_now(self, deal_id: str, now: datetime) -> dict:
        """Agent-triggered reminder, rate-limited so the model cannot spam the buyer."""
        with self._lock:
            if self._status(deal_id) == "escalated":
                return {"sent": False, "reason": "deal was handed to the sales team; they will follow up"}
            audit = self.audit_deal(deal_id)
            if audit.complete:
                return {"sent": False, "reason": "contract is already complete"}
            last = self._last_sent(deal_id)
            gap = timedelta(hours=self.config.min_hours_between)
            if last and now - last < gap:
                return {"sent": False, "reason": f"last reminder was sent at {_iso(last)}; "
                                                 f"next one allowed after {_iso(last + gap)}"}
            self.start(deal_id, now)
            if self._attempts(deal_id) >= self.config.escalate_after:
                return {"sent": False, "reason": "maximum number of reminders reached"}
            return {"sent": True, **self._send(deal_id, audit, now, "agent")}

    def run_due(self, now: datetime) -> list[dict]:
        """Process every active sequence. Call from cron / a scheduler, e.g. hourly."""
        with self._lock:
            events = []
            rows = self.db.execute("SELECT deal_id, started_at FROM sequences WHERE status='active'").fetchall()
            for deal_id, started_at in rows:
                try:
                    events.extend(self._process(deal_id, started_at, now))
                except Exception as exc:  # one broken contract must not stop reminders for every other deal
                    log.exception("reminder run failed for deal %s", deal_id)
                    events.append({"deal_id": deal_id, "event": "error", "error": str(exc)})
            return events

    def _process(self, deal_id: str, started_at: str, now: datetime) -> list[dict]:
        audit = self.audit_deal(deal_id)
        if audit.complete:
            self._set_status(deal_id, "done")
            self.crm.add_note(deal_id, "Contract audit passed - reminder sequence stopped.")
            return [{"deal_id": deal_id, "event": "completed"}]
        attempts = self._attempts(deal_id)
        if attempts >= self.config.escalate_after:
            self._escalate(deal_id, audit, attempts)
            return [{"deal_id": deal_id, "event": "escalated"}]
        if attempts >= len(self.config.schedule_days):
            return []
        due = datetime.fromisoformat(started_at) + timedelta(days=self.config.schedule_days[attempts])
        last = self._last_sent(deal_id)
        too_soon = last and now - last < timedelta(hours=self.config.min_hours_between)
        if now >= due and not too_soon:
            return [{"deal_id": deal_id, "event": "sent", **self._send(deal_id, audit, now, "schedule")}]
        return []

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
