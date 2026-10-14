"""Acceptance criterion 2: replay the client's quote sheet through the engine.

Usage:  python scripts/validate_pricing.py [path/to/reference_quotes.csv]
Writes docs/pricing_variance_report.md and exits non-zero if any row exceeds the tolerance.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from closing_agent.pricing import validate_against_spreadsheet  # noqa: E402
from closing_agent.services import Services  # noqa: E402


def main() -> int:
    s = Services()
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "reference_quotes.csv"
    tol = s.settings.raw["price_tolerance_pct"]
    rows = validate_against_spreadsheet(s.matrix, csv_path, tol)
    worst = max(abs(r.variance_pct) for r in rows)
    lines = [
        "# Pricing variance report", "",
        f"Source sheet: `{csv_path.name}` · price list: `{Path(s.matrix.source).name}` · tolerance ±{tol}%", "",
        f"**{sum(r.ok for r in rows)}/{len(rows)} rows within tolerance · largest variance {worst}%**", "",
        "| Ref | SKU | Qty | Term | Sheet total | Engine total | Variance % | OK |",
        "|---|---|---:|---:|---:|---:|---:|:-:|",
    ]
    for r in rows:
        lines.append(f"| {r.ref} | {r.sku} | {r.quantity} | {r.term_months} | {r.spreadsheet_total} | "
                     f"{r.engine_total} | {r.variance_pct} | {'✓' if r.ok else '✗'} |")
    lines += ["", "Small non-zero variances come from rounding: the engine rounds the unit price to pence "
              "(as printed on invoices), the spreadsheet rounds only the final total."]
    out = ROOT / "docs" / "pricing_variance_report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
