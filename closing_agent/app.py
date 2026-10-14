"""HTTP API + the two embeddable pages.

  /widget?deal=D-1002&t=<token>     buyer-facing chat (link goes into reminder e-mails)
  /sidebar?deal=D-1002#key=<admin>  sales-rep panel, embeddable as a CRM sidebar iframe

Buyer endpoints are authorised by a per-deal HMAC token; rep/admin endpoints by
the X-Admin-Key header (CLOSING_ADMIN_KEY).

Run:  uvicorn closing_agent.app:app --reload
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from .agent import ClosingAgent
from .crm import DealNotFound
from .services import ROOT, Services

log = logging.getLogger(__name__)
WIDGET_DIR = ROOT / "widget"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def create_app(services: Services | None = None, agent: ClosingAgent | None = None) -> FastAPI:
    app = FastAPI(title="Closing Agent", version="0.1.0")
    s = services or Services()
    state = {"agent": agent}

    def get_agent() -> ClosingAgent:
        if state["agent"] is None:
            try:
                state["agent"] = ClosingAgent(s)
            except Exception as exc:  # missing credentials etc.
                raise HTTPException(503, f"Claude client not configured: {exc}") from exc
        return state["agent"]

    def deal_auth(deal_id: str, t: str = Query("")) -> str:
        try:
            s.crm.get_deal(deal_id)
        except DealNotFound:
            raise HTTPException(404, "deal not found") from None
        if not s.check_token(deal_id, t):
            raise HTTPException(403, "invalid link")
        return deal_id

    def admin_auth(x_admin_key: str = Header("")) -> None:
        expected = os.environ.get("CLOSING_ADMIN_KEY")
        if not expected:
            raise HTTPException(503, "CLOSING_ADMIN_KEY is not set")
        if not hmac.compare_digest(expected, x_admin_key):
            raise HTTPException(403, "invalid admin key")

    # ---------- pages ----------

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/docs")

    @app.get("/widget", include_in_schema=False)
    def widget():
        return FileResponse(WIDGET_DIR / "widget.html")

    @app.get("/sidebar", include_in_schema=False)
    def sidebar():
        return FileResponse(WIDGET_DIR / "sidebar.html")

    # ---------- buyer API ----------

    @app.get("/api/deals/{deal_id}")
    def deal_status(deal_id: str = Depends(deal_auth)):
        deal = s.crm.get_deal(deal_id)
        audit = s.audit_deal(deal_id)
        quote = s.matrix.quote(deal["sku"], deal["quantity"], deal["term_months"],
                               deal.get("extra_discount_pct", 0))
        return {"deal": {k: deal[k] for k in ("id", "company", "contact", "stage", "sku", "quantity",
                                               "term_months")},
                "price": quote.to_dict(),
                "contract": {"complete": audit.complete, "mode": audit.mode,
                             "open_items": audit.summary_lines()}}

    @app.post("/api/deals/{deal_id}/chat")
    def chat(body: ChatIn, deal_id: str = Depends(deal_auth)):
        try:
            reply = get_agent().chat(deal_id, body.message)
        except anthropic.RateLimitError:
            raise HTTPException(429, "The assistant is busy - please try again in a minute.") from None
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as exc:
            log.exception("Claude API error for deal %s", deal_id)
            raise HTTPException(502, "The assistant is temporarily unavailable.") from exc
        except TypeError as exc:  # SDK raises this when no credentials are configured
            if "authentication" not in str(exc):
                raise
            log.error("Claude credentials missing: %s", exc)
            raise HTTPException(503, "The assistant is not configured yet.") from exc
        return {"reply": reply.text, "tools": [c["tool"] for c in reply.tool_calls],
                "stop_reason": reply.stop_reason}

    @app.post("/api/deals/{deal_id}/contract")
    async def upload_contract(file: UploadFile = File(...), deal_id: str = Depends(deal_auth)):
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file too large")
        if not data.startswith(b"%PDF"):
            raise HTTPException(415, "please upload a PDF")
        folder = s.settings.path("uploads")
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        target = folder / f"{deal_id}_{stamp}.pdf"  # never trust the client's filename
        target.write_bytes(data)
        s.crm.update_deal(deal_id, contract=str(target.relative_to(s.settings.root)), stage="contract_sent")
        s.crm.add_note(deal_id, f"Contract uploaded via widget ({_safe(file.filename)}).")
        audit = s.audit_deal(deal_id)
        if not audit.complete:
            s.nudges.start(deal_id, datetime.now(UTC))
        return {"complete": audit.complete, "open_items": audit.summary_lines()}

    # ---------- rep / admin API ----------

    @app.get("/api/admin/deals/{deal_id}", dependencies=[Depends(admin_auth)])
    def rep_view(deal_id: str):
        try:
            deal = s.crm.get_deal(deal_id)
        except DealNotFound:
            raise HTTPException(404, "deal not found") from None
        audit = s.audit_deal(deal_id)
        return {"deal": deal, "audit": audit.to_dict(), "open_items": audit.summary_lines(),
                "price_options": s.matrix.price_options(deal["sku"], deal["quantity"]),
                "reminders": s.nudges.status(deal_id), "notes": s.crm.notes(deal_id),
                "tasks": s.crm.tasks(deal_id), "widget_link": s.widget_link(deal_id)}

    @app.post("/api/admin/deals/{deal_id}/start-reminders", dependencies=[Depends(admin_auth)])
    def start_reminders(deal_id: str):
        s.nudges.start(deal_id, datetime.now(UTC))
        return {"events": s.nudges.run_due(datetime.now(UTC))}

    @app.post("/api/admin/nudges/run", dependencies=[Depends(admin_auth)])
    def run_nudges():
        """Call from cron / a scheduler (e.g. hourly)."""
        return {"events": s.nudges.run_due(datetime.now(UTC))}

    @app.get("/api/admin/links", dependencies=[Depends(admin_auth)])
    def links():
        return {d: s.widget_link(d) for d in s.crm.list_deals()}

    return app


def _safe(name: str | None) -> str:
    return re.sub(r"[^\w.\- ]", "_", Path(name or "upload.pdf").name)[:80]


app = create_app() if os.environ.get("CLOSING_NO_AUTOAPP") != "1" else None
