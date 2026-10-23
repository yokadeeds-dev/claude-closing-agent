# Brief — Claude closing agents

Goal: Claude-powered agents that take prospects from "verbal yes" to a fully closed deal without human back-and-forth.

The agents should:

- pick up the sales conversation at the closing stage, use the pricing matrix, product comparison sheets and supporting documents, and present the most suitable price option;
- read Excel/CSV matrices and PDF or Google Docs contracts, compare prices in real time and surface discrepancies;
- confirm that every required contract field is completed, flag missing signatures or initials, and remind the buyer automatically until everything is in order.

A cloud CRM is in place; connecting Claude through its API or a lightweight middleware layer is preferred.

## Acceptance criteria

1. Deployed agent(s) accessible through a CRM sidebar or a simple web widget.
2. Price-comparison logic validated against existing spreadsheets (≤ 1 % variance).
3. Automated document audit that reliably identifies incomplete sections in sample contracts.
4. Hand-off guide so the sales team can adjust prompts and add pricing rows without coding.
