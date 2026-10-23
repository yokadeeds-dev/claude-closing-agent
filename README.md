# Closing Agent

Claude-powered agent that takes a prospect from **"verbal yes" to a complete, signed contract**: presents exact prices from the client's pricing matrix, audits contracts for missing fields, signatures and initials, nudges the buyer until everything is in order, and only then marks the deal won.

Requirements and acceptance criteria: [docs/BRIEF.md](docs/BRIEF.md).

## How it meets the acceptance criteria

| # | Criterion | Where | Evidence |
|---|---|---|---|
| 1 | Agent reachable via CRM sidebar or web widget | `widget/widget.html` (buyer chat + upload), `widget/sidebar.html` (rep panel, iframe-embeddable) | `uvicorn closing_agent.app:app` |
| 2 | Price logic ≤ 1 % variance vs. existing spreadsheets | `closing_agent/pricing.py` — Decimal maths, never the LLM | `scripts/validate_pricing.py` → [variance report](docs/pricing_variance_report.md): 12/12 rows, max 0.0225 % |
| 3 | Audit reliably finds incomplete sections | `closing_agent/audit.py` — AcroForm + flat-PDF text mode, price and CRM cross-checks | `tests/test_audit.py` on 4 sample contracts |
| 4 | Hand-off guide, no coding needed | Excel workbook + YAML/Markdown config | [docs/HANDOFF.md](docs/HANDOFF.md) |

## Architecture

```
Buyer ──► widget ──► FastAPI ──► ClosingAgent (Claude tool-use loop, one deal per session)
Rep ────► sidebar ─┘                │
                                    ├─ pricing.py   deterministic quotes, options, upsell, price compare
                                    ├─ audit.py     fields / signatures / initials / price / order consistency
                                    ├─ nudges.py    reminder sequence (SQLite state, stops when audit passes)
                                    └─ crm.py       CRM protocol (JsonCRM demo; HubSpot/Pipedrive adapter = same 5 methods)
```

Design decisions:

- **The LLM never does arithmetic.** Claude presents numbers that come from tools. This is the only way to *prove* the ≤ 1 % criterion.
- **Guardrails live in code, not in the prompt** — discount limit, "closed won only when the audit passes", reminder rate limit, deal scoping. A prompt can be talked around; a function cannot.
- **Tools are bound to the session's deal.** No tool takes a deal ID, so a buyer cannot reach another customer's data. Widget links are per-deal HMAC tokens.
- **Deterministic audit, LLM explanation.** Whether a contract is complete is decided by rules; Claude turns the findings into a friendly message.
- **Refusals and budget overruns hand off to a person** (CRM task) instead of failing silently. Server-side refusal fallback is enabled.
- **Append-only conversation history** per deal, so prompt caching and preserved thinking keep working across turns.

## Run it

```bash
python -m venv .venv && .venv/Scripts/activate        # Windows; use bin/activate elsewhere
pip install -e ".[dev]"
python scripts/make_sample_data.py                     # demo workbook, contracts, CRM
cp .env.example .env                                   # set ANTHROPIC_API_KEY, CLOSING_WIDGET_SECRET, CLOSING_ADMIN_KEY
uvicorn closing_agent.app:app --reload --env-file .env   # --env-file loads .env (python-dotenv ships with uvicorn[standard])
```

- Buyer links: `GET /api/admin/links` (header `X-Admin-Key`)
- Rep sidebar: `http://localhost:8000/sidebar?deal=D-1002#key=<CLOSING_ADMIN_KEY>`
- API docs: `http://localhost:8000/docs`

Demo deals: **D-1001** complete · **D-1002** missing e-mail, signature, initials · **D-1003** price 8 % below the price list · **D-1004** flat Google-Docs PDF, quantity differs from the CRM.

## Live demo

```bash
python scripts/live_demo.py D-1002   # 4-turn buyer conversation, writes docs/live_run.md (a few cents)
```

## Tests

```bash
pytest          # 55 tests, no API key needed (Claude is replaced by a scripted fake client)
ruff check .
```

## Agent evaluation

A golden set of 16 buyer conversations graded on end state, tool calls, invented prices, required facts and a judged rubric — run against the real agent. Opus 5.5, Sonnet 5.5 and Haiku 5.5 all pass 16/16; cost per buyer turn $0.029 / $0.013 / $0.0008. What that does and does not prove: [eval/RESULTS.md](eval/RESULTS.md).

```bash
python -m eval.run --variant baseline        # ~$0.50 on Opus 5.5
```

## Status

| Milestone | Status |
|---|---|
| M1 Discovery & data mapping | Simulated with demo data (`scripts/make_sample_data.py`) |
| M2 Pricing engine + variance report | ✅ done |
| M3 Document audit | ✅ done (form + flat PDF). Google Docs: export to PDF via Drive API — not wired |
| M4 Closing agent + reminders + guardrails | ✅ done — unit-tested with a fake client and verified live against the Claude API ([transcript](docs/live_run.md)) |
| M5 Widget, sidebar, hand-off guide | ✅ done; real CRM adapter (client's CRM unknown) and deployment pending |

Not done (would need client input): real CRM adapter, e-mail delivery (SMTP/CRM API instead of `data/outbox.jsonl`), e-sign webhook, scanned-PDF signature detection (vision), persistent chat sessions across restarts, inbound rate limit on `/chat`, audit log.
