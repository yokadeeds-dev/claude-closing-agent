# Closing-agent eval — results

2026-10-14 · 16 cases ([golden_set.yaml](golden_set.yaml)), 17 buyer turns, 1 run per variant · judge `claude-sonnet-5-5`

## What is measured

The agent acts on an environment, so cases are graded mainly on the **end state** and on **tool calls**:

| Metric | How | Example |
|---|---|---|
| `tools` | expected / forbidden tool calls and arguments | "10 seats, 24 months, record it" → `select_option(CRM-STD, 10, 24)`; "not sure yet" → no `select_option` |
| `state` | CRM stage, discount, tasks, reminders after the conversation | 10 % request → discount stays 0, a manager task exists |
| `prices` | every £ amount in the replies appears in a tool result or in the buyer's message | catches prices the model computed itself |
| `mentions` | required facts in the final reply | open contract items with page numbers |
| `rubric` | yes/no statements, Claude judge with structured outputs | "does NOT promise the 10 % discount" |

`pass` = every applicable check is 1. Groups: contract, pricing, discount (incl. a prompt-injection case), hand-off, reminders (incl. a rate-limit case).

## Model comparison

| Agent model | pass | cost / run (16 cases) | cost / buyer turn | latency median / max |
|---|---:|---:|---:|---:|
| `claude-opus-5-5` (default) | 16/16 | $0.48 | $0.029 | 9.3 s / 18.8 s |
| `claude-sonnet-5-5` | 16/16 | $0.22 | $0.013 | 6.5 s / 11.4 s |
| `claude-haiku-5-5` | 16/16 | $0.013 | $0.0008 | 5.7 s / 10.3 s |

Cost from the API's `usage` (cache reads 0.1×, cache writes 1.25× input price).

**Reading this honestly:** on this set all three models pass, so the eval **cannot rank them** — it is saturated. With 16 cases and one run, a difference below roughly ±25 percentage points would be noise. The result supports the proposal's claim that Sonnet 5.5 is a viable cheaper option; it does **not** yet justify switching production to Haiku. Before that: harder cases (multi-turn negotiation, ambiguous order changes, contract re-issue flows) and 3 runs per case.

## Grader checks (before trusting the numbers)

- **Null run** (`--mode null`, an "agent" that does nothing): 0/16 pass — after adding a required tool call to P04, which had passed by doing nothing. Runs in CI (`tests/test_eval_graders.py`).
- **Two grader false negatives fixed**, both found by reading failing outputs rather than accepting the score:
  - C01 required the word "signature"; the agent wrote "Sign the contract" → alternatives allowed.
  - C04 rubric required "price list"; Haiku wrote "doesn't match the agreed order" with the correct price → statement rephrased to the substance, **all three variants re-judged** (`--rejudge`) so they are scored by the same grader.

## Run it

```bash
python -m eval.run --mode null --variant check_null          # free, harness check
python -m eval.run --variant baseline                        # agent + judge, ~$0.50 on Opus 5.5
CLOSING_MODEL=claude-sonnet-5-5 python -m eval.run --variant v1
python -m eval.run --variant v1 --rejudge                     # after editing a rubric (judge only)
```
