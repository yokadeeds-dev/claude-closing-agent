# Pricing variance report

Source sheet: `reference_quotes.csv` · price list: `pricing_matrix.xlsx` · tolerance ±1.0%

**12/12 rows within tolerance · largest variance 0.0225%**

| Ref | SKU | Qty | Term | Sheet total | Engine total | Variance % | OK |
|---|---|---:|---:|---:|---:|---:|:-:|
| Q-2301 | CRM-STD | 8 | 12 | 2505.60 | 2505.60 | 0.0000 | ✓ |
| Q-2302 | CRM-STD | 30 | 24 | 15973.20 | 15976.80 | 0.0225 | ✓ |
| Q-2303 | CRM-PRO | 12 | 12 | 6032.88 | 6033.60 | 0.0119 | ✓ |
| Q-2304 | CRM-PRO | 55 | 36 | 65973.60 | 65973.60 | 0.0000 | ✓ |
| Q-2305 | CRM-PRO | 3 | 1 | 147.00 | 147.00 | 0.0000 | ✓ |
| Q-2306 | CRM-ENT | 20 | 24 | 33407.04 | 33408.00 | 0.0029 | ✓ |
| Q-2307 | CRM-ENT | 120 | 36 | 261446.40 | 261446.40 | 0.0000 | ✓ |
| Q-2308 | ADD-AI | 26 | 12 | 3790.80 | 3790.80 | 0.0000 | ✓ |
| Q-2309 | ADD-AI | 7 | 24 | 2142.00 | 2142.00 | 0.0000 | ✓ |
| Q-2310 | SVC-ONB | 1 | 12 | 1500.00 | 1500.00 | 0.0000 | ✓ |
| Q-2311 | CRM-STD | 49 | 36 | 36832.32 | 36832.32 | 0.0000 | ✓ |
| Q-2312 | CRM-ENT | 7 | 12 | 6728.40 | 6728.40 | 0.0000 | ✓ |

Small non-zero variances come from rounding: the engine rounds the unit price to pence (as printed on invoices), the spreadsheet rounds only the final total.
