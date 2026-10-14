"""Wires settings, pricing, audit, CRM and reminders together."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

from .audit import AuditResult, audit_contract, load_templates
from .crm import JsonCRM
from .nudges import NudgeConfig, NudgeService, OutboxSender
from .pricing import PriceMatrix

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)


@dataclass
class Settings:
    raw: dict
    root: Path

    @classmethod
    def load(cls, path: str | Path | None = None, root: Path = ROOT) -> Settings:
        path = Path(path or os.environ.get("CLOSING_SETTINGS") or root / "config" / "settings.yaml")
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls(raw, root)

    def path(self, key: str) -> Path:
        p = Path(self.raw["paths"][key])
        return p if p.is_absolute() else self.root / p

    @property
    def model(self) -> str:
        return os.environ.get("CLOSING_MODEL") or self.raw["model"]

    @property
    def effort(self) -> str:
        return os.environ.get("CLOSING_EFFORT") or self.raw["effort"]


class Services:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        s = self.settings
        self.matrix = PriceMatrix.from_xlsx(s.path("pricing_matrix"), vat_rate=s.raw["vat_rate"])
        self.templates = load_templates(s.path("contract_templates"))
        self.crm = JsonCRM(s.path("crm"))
        secret = os.environ.get("CLOSING_WIDGET_SECRET")
        if not secret:
            # Dev fallback only: links die on restart and differ between workers.
            log.warning("CLOSING_WIDGET_SECRET is not set - buyer links will stop working after a restart "
                        "and are not shared between workers. Set it in production.")
            secret = secrets.token_hex(16)
        self.secret = secret.encode()
        with open(s.path("nudge_messages"), encoding="utf-8") as fh:
            messages = yaml.safe_load(fh)
        n = s.raw["nudges"]
        self.nudges = NudgeService(
            s.path("state_db"), NudgeConfig(n["schedule_days"], n["min_hours_between"], n["escalate_after"]),
            messages, self.crm, self.audit_deal, OutboxSender(s.path("outbox")), link_for=self.widget_link)

    @cached_property
    def system_prompt(self) -> str:
        text = self.settings.path("system_prompt").read_text(encoding="utf-8")
        return text.replace("{catalog}", self.matrix.catalog_text())

    def audit_deal(self, deal_id: str) -> AuditResult:
        deal = self.crm.get_deal(deal_id)
        path = Path(deal["contract"])
        path = path if path.is_absolute() else self.settings.root / path
        return audit_contract(path, deal["template"], self.templates, self.matrix, deal,
                              self.settings.raw["price_tolerance_pct"])

    # ---------- widget links ----------

    def token(self, deal_id: str) -> str:
        return hmac.new(self.secret, deal_id.encode(), hashlib.sha256).hexdigest()[:32]

    def check_token(self, deal_id: str, token: str) -> bool:
        return hmac.compare_digest(self.token(deal_id), token or "")

    def widget_link(self, deal_id: str) -> str:
        base = os.environ.get("CLOSING_PUBLIC_URL", "http://localhost:8000")
        return f"{base}/widget?deal={deal_id}&t={self.token(deal_id)}"
