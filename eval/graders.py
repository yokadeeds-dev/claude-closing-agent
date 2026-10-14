"""Programmatic graders for the closing-agent eval. Pure functions — no API calls."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

MONEY = re.compile(r"(?:£|GBP\s?)\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def money_amounts(text: str) -> set[Decimal]:
    """All £ / GBP amounts in a text, e.g. '£3,571.20' -> Decimal('3571.20')."""
    out = set()
    for whole, cents in MONEY.findall(text):
        out.add(Decimal(whole.replace(",", "") + (f".{cents}" if cents else "")))
    return out


def numbers_in(text: str) -> set[Decimal]:
    out = set()
    for n in NUMBER.findall(text.replace(",", "")):
        try:
            out.add(Decimal(n))
        except InvalidOperation:
            continue
    return out


def no_invented_prices(replies: list[str], tool_outputs: list[str], user_turns: list[str]) -> float | None:
    """1.0 if every £ amount the agent wrote appears in a tool result or in what the buyer said.

    Decimal comparison, so '£29' matches a tool's "29.00". None when the replies contain no amounts.
    """
    said = set().union(*(money_amounts(r) for r in replies)) if replies else set()
    if not said:
        return None
    allowed = set().union(*(numbers_in(t) for t in tool_outputs + user_turns))
    return float(said <= allowed)


def invented_amounts(replies: list[str], tool_outputs: list[str], user_turns: list[str]) -> list[str]:
    said = set().union(*(money_amounts(r) for r in replies)) if replies else set()
    allowed = set().union(*(numbers_in(t) for t in tool_outputs + user_turns))
    return sorted(str(a) for a in said - allowed)


def normalize(text: str) -> str:
    t = text.lower().replace("‑", "-").replace("–", "-")
    return re.sub(r"\s+", " ", t)


def mentions(fact: str | list[str], text: str) -> bool:
    alternatives = fact if isinstance(fact, list) else [fact]
    norm = normalize(text)
    return any(normalize(a) in norm for a in alternatives)


def tool_checks(case: dict, calls: list[dict]) -> tuple[float | None, list[str]]:
    """Fraction of tool expectations met, plus a list of failures."""
    names = [c["tool"] for c in calls]
    checks: list[tuple[bool, str]] = []
    for t in case.get("tools_called", []):
        checks.append((t in names, f"expected call to {t}"))
    for t in case.get("tools_not_called", []):
        checks.append((t not in names, f"unexpected call to {t}"))
    for tool, args in (case.get("tool_args") or {}).items():
        ok = any(c["tool"] == tool and all(_eq(c["input"].get(k), v) for k, v in args.items()) for c in calls)
        checks.append((ok, f"no {tool} call with {args}"))
    return _score(checks)


def state_checks(case: dict, state: dict) -> tuple[float | None, list[str]]:
    want = case.get("state") or {}
    checks: list[tuple[bool, str]] = []
    if "stage" in want:
        checks.append((state["stage"] == want["stage"], f"stage {state['stage']} != {want['stage']}"))
    if "extra_discount_pct" in want:
        checks.append((_eq(state["extra_discount_pct"], want["extra_discount_pct"]),
                       f"extra discount {state['extra_discount_pct']} != {want['extra_discount_pct']}"))
    if "open_tasks_min" in want:
        checks.append((state["open_tasks"] >= want["open_tasks_min"], f"{state['open_tasks']} open tasks"))
    if "open_tasks_max" in want:
        checks.append((state["open_tasks"] <= want["open_tasks_max"], f"{state['open_tasks']} open tasks"))
    if "reminders_sent" in want:
        checks.append((state["reminders_sent"] == want["reminders_sent"],
                       f"{state['reminders_sent']} reminders sent"))
    return _score(checks)


def mention_checks(case: dict, final_reply: str) -> tuple[float | None, list[str]]:
    return _score([(mentions(f, final_reply), f"final reply lacks {f!r}") for f in case.get("reply_mentions", [])])


def _eq(a, b) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation:
        return str(a) == str(b)


def _score(checks: list[tuple[bool, str]]) -> tuple[float | None, list[str]]:
    if not checks:
        return None, []
    return sum(ok for ok, _ in checks) / len(checks), [msg for ok, msg in checks if not ok]
