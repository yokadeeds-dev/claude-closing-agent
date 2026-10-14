"""CRM adapter.

The agent only talks to the `CRM` protocol below. `JsonCRM` is the demo backend;
a real integration (HubSpot, Pipedrive, Salesforce, ...) implements the same five
methods against that CRM's REST API and is selected in services.py.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol


class DealNotFound(KeyError):
    pass


class CRM(Protocol):
    def get_deal(self, deal_id: str) -> dict: ...
    def update_deal(self, deal_id: str, **changes) -> dict: ...
    def add_note(self, deal_id: str, text: str) -> None: ...
    def create_task(self, deal_id: str, title: str, body: str) -> dict: ...
    def list_deals(self) -> dict[str, dict]: ...


STAGES = ("verbal_yes", "option_selected", "contract_sent", "closed_won", "needs_human")


class JsonCRM:
    """File-backed CRM for the demo and tests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def list_deals(self) -> dict[str, dict]:
        return self._load()["deals"]

    def get_deal(self, deal_id: str) -> dict:
        deals = self.list_deals()
        if deal_id not in deals:
            raise DealNotFound(deal_id)
        return {"id": deal_id, **deals[deal_id]}

    def update_deal(self, deal_id: str, **changes) -> dict:
        if "stage" in changes and changes["stage"] not in STAGES:
            raise ValueError(f"Unknown stage {changes['stage']!r}")
        with self._lock:
            data = self._load()
            if deal_id not in data["deals"]:
                raise DealNotFound(deal_id)
            data["deals"][deal_id].update(changes)
            self._save(data)
        return self.get_deal(deal_id)

    def add_note(self, deal_id: str, text: str) -> None:
        with self._lock:
            data = self._load()
            data["notes"].append({"deal_id": deal_id, "at": _now(), "text": text})
            self._save(data)

    def create_task(self, deal_id: str, title: str, body: str) -> dict:
        with self._lock:
            data = self._load()
            task = {"id": f"T-{len(data['tasks']) + 1:04d}", "deal_id": deal_id, "at": _now(),
                    "title": title, "body": body, "status": "open"}
            data["tasks"].append(task)
            self._save(data)
        return task

    def notes(self, deal_id: str) -> list[dict]:
        return [n for n in self._load()["notes"] if n["deal_id"] == deal_id]

    def tasks(self, deal_id: str) -> list[dict]:
        return [t for t in self._load()["tasks"] if t["deal_id"] == deal_id]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
