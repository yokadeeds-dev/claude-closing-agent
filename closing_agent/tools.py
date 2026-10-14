"""Tools the closing agent can call.

Security model: every tool is bound to ONE deal - the deal of the chat session.
The model never passes a deal ID, so a buyer cannot talk the agent into reading
or changing someone else's deal. Guardrails (discount limit, "closed won only
when the audit passes", reminder rate limit) are enforced here in code, not in
the prompt.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal

from .pricing import PricingError
from .services import Services

log = logging.getLogger(__name__)


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


TOOLS = [
    {"name": "get_deal_summary", "strict": True,
     "description": "Current deal: company, contact, agreed order line (sku, quantity, term), stage, "
                    "contract status and open items. Call this first in every conversation.",
     "input_schema": _schema({})},
    {"name": "get_price_options", "strict": True,
     "description": "Exact prices from the price list for a product and quantity: one option per contract "
                    "term, plus a volume upsell when the next discount tier is close. Always use these "
                    "numbers; never calculate prices yourself.",
     "input_schema": _schema({"sku": {"type": "string"}, "quantity": {"type": "integer"}})},
    {"name": "compare_price", "strict": True,
     "description": "Check a price the buyer mentions (from an old quote, e-mail or competitor-adjusted "
                    "offer) against the price list. Returns the expected total and the variance.",
     "input_schema": _schema({"sku": {"type": "string"}, "quantity": {"type": "integer"},
                              "term_months": {"type": "integer"},
                              "offered_total_net": {"type": "string",
                                                    "description": "Net total as a decimal, e.g. 15976.80"}})},
    {"name": "select_option", "strict": True,
     "description": "Record the option the buyer has chosen (updates the order line on the deal). "
                    "Only call after the buyer has clearly confirmed product, quantity and term.",
     "input_schema": _schema({"sku": {"type": "string"}, "quantity": {"type": "integer"},
                              "term_months": {"type": "integer"}})},
    {"name": "request_extra_discount", "strict": True,
     "description": "Ask for a discount on top of the price list. Small discounts within policy are "
                    "approved immediately; larger ones are sent to a sales manager for approval.",
     "input_schema": _schema({"extra_discount_pct": {"type": "number"},
                              "reason": {"type": "string"}})},
    {"name": "audit_contract", "strict": True,
     "description": "Check the deal's contract: required fields, signatures, initials, price and order "
                    "consistency. Returns whether it is complete and a list of open items with page numbers.",
     "input_schema": _schema({})},
    {"name": "send_reminder", "strict": True,
     "description": "E-mail the buyer a reminder listing the open contract items. Rate-limited; returns "
                    "whether it was sent.",
     "input_schema": _schema({})},
    {"name": "mark_closed_won", "strict": True,
     "description": "Mark the deal as closed won. Only succeeds when the contract audit passes.",
     "input_schema": _schema({})},
    {"name": "hand_off_to_human", "strict": True,
     "description": "Create a task for the sales team when the buyer asks for something outside your "
                    "remit (legal changes, custom terms, complaints) or explicitly wants a person.",
     "input_schema": _schema({"reason": {"type": "string"}})},
]


class DealTools:
    def __init__(self, services: Services, deal_id: str):
        self.s = services
        self.deal_id = deal_id

    def run(self, name: str, args: dict) -> str:
        """Execute a tool and return JSON text. Raises ToolError for expected failures."""
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            raise ToolError(f"Unknown tool {name!r}")
        try:
            return json.dumps(handler(**args), default=str)
        except (PricingError, ValueError, KeyError, TypeError) as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:  # e.g. missing/corrupt contract PDF, database error
            log.exception("tool %s failed for deal %s", name, self.deal_id)
            raise ToolError(f"{name} is temporarily unavailable ({type(exc).__name__}); "
                            "offer to hand off to the sales team") from exc

    # ---------- handlers ----------

    def _t_get_deal_summary(self) -> dict:
        deal = self.s.crm.get_deal(self.deal_id)
        audit = self.s.audit_deal(self.deal_id)
        quote = self.s.matrix.quote(deal["sku"], deal["quantity"], deal["term_months"],
                                    deal.get("extra_discount_pct", 0))
        return {
            "company": deal["company"], "contact": deal["contact"], "stage": deal["stage"],
            "order": {"sku": deal["sku"], "quantity": deal["quantity"], "term_months": deal["term_months"],
                      "extra_discount_pct": deal.get("extra_discount_pct", 0)},
            "price": quote.to_dict(),
            "contract": {"complete": audit.complete, "open_items": audit.summary_lines()},
            "reminders": self.s.nudges.status(self.deal_id),
        }

    def _t_get_price_options(self, sku: str, quantity: int) -> dict:
        return self.s.matrix.price_options(sku, quantity)

    def _t_compare_price(self, sku: str, quantity: int, term_months: int, offered_total_net: str) -> dict:
        return self.s.matrix.compare(sku, quantity, term_months, offered_total_net,
                                     self.s.settings.raw["price_tolerance_pct"])

    def _t_select_option(self, sku: str, quantity: int, term_months: int) -> dict:
        quote = self.s.matrix.quote(sku, quantity, term_months)  # validates sku / term / quantity
        deal = self.s.crm.get_deal(self.deal_id)
        # a new order line voids any discount approved for the old one
        changes = {"sku": sku, "quantity": quantity, "term_months": term_months, "extra_discount_pct": 0}
        if deal["stage"] == "verbal_yes":
            changes["stage"] = "option_selected"
        self.s.crm.update_deal(self.deal_id, **changes)
        self.s.crm.add_note(self.deal_id, f"Buyer selected {quantity} x {sku}, {term_months} months "
                                          f"(net {quote.total_net}).")
        return {"recorded": True, "price": quote.to_dict(),
                "note": "If a contract was already issued, it must be re-issued with this order line."}

    def _t_request_extra_discount(self, extra_discount_pct: float, reason: str) -> dict:
        pct = Decimal(str(extra_discount_pct))
        limit = Decimal(str(self.s.settings.raw["max_extra_discount_pct"]))
        deal = self.s.crm.get_deal(self.deal_id)
        if pct <= 0:
            raise ValueError("extra_discount_pct must be positive")
        if pct <= limit:
            quote = self.s.matrix.quote(deal["sku"], deal["quantity"], deal["term_months"], pct)
            self.s.crm.update_deal(self.deal_id, extra_discount_pct=float(pct))
            self.s.crm.add_note(self.deal_id, f"Extra discount {pct}% approved by policy. Reason: {reason}")
            return {"status": "approved", "extra_discount_pct": str(pct), "price": quote.to_dict()}
        task = self.s.crm.create_task(
            self.deal_id, f"Discount approval: {pct}% for {deal['company']}",
            f"Buyer asked for {pct}% extra discount (policy limit {limit}%). Reason: {reason}")
        return {"status": "pending_approval", "policy_limit_pct": str(limit), "task_id": task["id"],
                "message": "A sales manager will review this. Do not promise the discount."}

    def _t_audit_contract(self) -> dict:
        audit = self.s.audit_deal(self.deal_id)
        return {"complete": audit.complete, "reading_mode": audit.mode, "open_items": audit.summary_lines()}

    def _t_send_reminder(self) -> dict:
        return self.s.nudges.send_now(self.deal_id, datetime.now(UTC))

    def _t_mark_closed_won(self) -> dict:
        audit = self.s.audit_deal(self.deal_id)
        if not audit.complete:
            return {"closed": False, "reason": "contract incomplete", "open_items": audit.summary_lines()}
        self.s.crm.update_deal(self.deal_id, stage="closed_won")
        self.s.crm.add_note(self.deal_id, "Deal closed won by closing agent (contract audit passed).")
        return {"closed": True}

    def _t_hand_off_to_human(self, reason: str) -> dict:
        deal = self.s.crm.get_deal(self.deal_id)
        task = self.s.crm.create_task(self.deal_id, f"Buyer needs a person: {deal['company']}", reason)
        return {"handed_off": True, "task_id": task["id"]}


class ToolError(Exception):
    pass
