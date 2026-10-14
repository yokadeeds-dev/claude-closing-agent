# Hand-off guide — for the sales team

Everything you will normally change lives in three places, and none of it needs coding:

| You want to… | Edit | Then |
|---|---|---|
| Change prices, add a product, change volume or term discounts | `data/pricing_matrix.xlsx` | run the price check (below) |
| Change how the assistant talks, what it may and may not do | `config/system_prompt.md` | restart the service |
| Change discount limit, VAT, reminder timing | `config/settings.yaml` | restart the service |
| Change reminder e-mail wording | `config/nudge_messages.yaml` | restart the service |
| Support a new contract template | `config/contract_templates.yaml` | test with a sample PDF |

## 1. Pricing workbook (`data/pricing_matrix.xlsx`)

Three sheets. Keep the header row exactly as it is.

**Products**

| sku | name | billing | list_price | highlights |
|---|---|---|---|---|
| CRM-PRO | Closer CRM Pro | monthly | 49 | Standard + automation, forecasting, 50 GB |

- `sku` — short unique code, no spaces. The assistant and contracts refer to products by this code.
- `billing` — `monthly` (price per unit per month) or `one_off` (price per unit, paid once).
- `list_price` — net price in GBP before any discount.
- `highlights` — one line the assistant may use to describe the product.

**VolumeTiers** — one row per discount step: `sku`, `min_qty`, `discount_pct`. A buyer with at least `min_qty` units gets that discount. Always include a `1 → 0` row.

**TermDiscounts** — `term_months`, `discount_pct`. Only these contract lengths can be offered. Applies to monthly products only.

**To add a new product:** add a row to *Products*, add its rows to *VolumeTiers*, save, run the price check.

### Price check (do this after every change)

```bash
python scripts/validate_pricing.py
```

It recalculates every quote in `data/reference_quotes.csv` (export of your existing quote sheet: `ref, sku, quantity, term_months, total_net`) and writes `docs/pricing_variance_report.md`. Every row must be within ±1 %. If one is not, either the workbook or the old quote is wrong — the report shows which row.

## 2. The assistant's instructions (`config/system_prompt.md`)

Plain English. Change tone, add a rule ("always mention the onboarding package for Enterprise"), or tighten what it may say. Leave the `{catalog}` line at the bottom — the current price list is inserted there automatically.

What you **cannot** loosen through the prompt, by design (these are enforced in code):

- Prices always come from the workbook — the assistant cannot invent or round a price.
- Extra discounts above `max_extra_discount_pct` always go to a manager as a CRM task.
- A deal is only marked *closed won* when the contract check passes.
- The assistant only ever sees the one deal it is chatting about.
- Reminders cannot be sent more often than `min_hours_between`.

## 3. Settings (`config/settings.yaml`)

| Setting | Meaning |
|---|---|
| `max_extra_discount_pct` | Discount the assistant may grant on its own, on top of the price list |
| `price_tolerance_pct` | How far a contract price may differ from the price list before it is flagged |
| `nudges.schedule_days` | Days after sending the contract on which reminders go out, e.g. `[0, 2, 5, 9]` |
| `nudges.escalate_after` | After this many reminders, a CRM task asks a person to call the buyer |
| `model` / `effort` | Which Claude model to use and how much it deliberates. `claude-opus-5-5` + `medium` is the default; `claude-sonnet-5-5` is cheaper per message |

## 4. Reminder e-mails (`config/nudge_messages.yaml`)

One block per reminder, friendliest first. Placeholders are filled in automatically: `{contact}` (first name), `{company}`, `{missing_list}` (the open items, with page numbers), `{link}` (the buyer's personal link to the chat and upload page).

## 5. Contract templates (`config/contract_templates.yaml`)

Each template lists the fields that must be filled, the signature fields, the initials per page, and which fields hold the order line (for the price check). Field names must match the form field names in your PDF. For flat PDFs (e.g. exported from Google Docs) the **labels** are matched instead — the line in the document must read exactly `Label: value`.

Test a new template:

```bash
python -c "from closing_agent.services import Services; from closing_agent.audit import audit_contract; s=Services(); a=audit_contract('path/to/sample.pdf','<template>',s.templates,s.matrix); print(a.complete); print(*a.summary_lines(), sep='\n')"
```

## 6. Day-to-day

- **Buyer link** — every reminder e-mail contains it; the sales sidebar has a *Copy buyer link* button.
- **Reminders** — run automatically when the scheduler calls `POST /api/admin/nudges/run` (hourly is fine). They stop by themselves as soon as the contract is complete.
- **Tasks for you** appear in the CRM when: a discount needs approval, a buyer asks for a person or for legal changes, a contract is still incomplete after the last reminder, or the assistant could not answer.
