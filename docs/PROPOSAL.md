# Proposal — Claude Closing Agents (freelancer.com #40759450)

Practice delivery: proposal written for a real, public brief ([docs/BRIEF.md](BRIEF.md)) and the solution built end to end in this repository. Not submitted to the client.

---

## 1. Bid text (~1,500 chars)

```text
Hi,

Quick note first: Claude-Instant and Claude-2 are retired. I'd build on the current Claude models — Sonnet 5.5 for the closing conversation, Haiku 5.5 for bulk document checks — which are far stronger and cheaper per deal. Rough running cost: well under $0.20 per closed deal.

How I'd hit your acceptance criteria:

1. Pricing ≤1% variance: prices are never "calculated" by the AI. A small Python engine reads your Excel/CSV matrix and does the maths deterministically; Claude only explains and presents the result. I validate it with an automated test that replays your existing spreadsheet rows — you get a variance report, not a promise.
2. Document audit: required fields, signatures and initials are checked per contract template (PDF form fields + text/layout checks, Google Docs via API). Every gap gets a clear "page 3, initials missing" finding.
3. Automatic nudges: reminder sequence (email or CRM task) that stops as soon as the audit passes.
4. CRM sidebar / web widget + hand-off guide: prompts and pricing rows live in plain config files / a Google Sheet your team edits without code.

Guardrail I'd recommend: discounts above a limit you set go to a human for one-click approval.

Relevant work: production-style RAG assistant (FastAPI, Claude API, Postgres/pgvector, CI/CD) and several MCP servers connecting Claude to real software — github.com/yokadeeds-dev

Three questions:
- Which CRM (HubSpot, Pipedrive, Salesforce, Zoho…)?
- How are contracts signed today — e-sign tool or scanned PDFs?
- Roughly how many deals per month?

Fixed price £2,150, 5 milestones, ~3 weeks. Happy to start with milestone 1.
```

## 2. Milestones

| # | Milestone | Deliverable |
|---|---|---|
| M1 | Discovery & data mapping | Pricing matrix, contract templates and CRM API access mapped; written spec |
| M2 | Pricing engine | Deterministic engine + variance report against the client's spreadsheets (≤ 1 %) |
| M3 | Document audit | Required-field / signature / initials checks per template, findings report |
| M4 | Closing agent + nudges | Claude tool-use agent, reminder sequence, discount guardrail |
| M5 | Widget, CRM integration, hand-off | Sidebar / web widget, deployment, hand-off guide, 1 h walkthrough |

Delivery status per milestone: see the [README](../README.md#status).
