"""The closing agent: a Claude tool-use loop bound to one deal per conversation.

A manual loop (rather than the SDK's beta tool runner) because each deal keeps a
long-lived, append-only conversation history across many HTTP requests, and we
want full control over refusals and the tool-call budget per turn.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field

import anthropic

from .services import Services
from .tools import TOOLS, DealTools, ToolError

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
FALLBACK_BETA = "server-side-fallback-2026-07-01"
HANDOFF_TEXT = ("Thanks - I've passed this to a member of our sales team, who will get back to you shortly.")


@dataclass
class Reply:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)


class ClosingAgent:
    def __init__(self, services: Services, client: anthropic.Anthropic | None = None):
        self.s = services
        self.client = client or anthropic.Anthropic()
        self.sessions: dict[str, list] = {}  # deal_id -> append-only message history
        self._locks: dict[str, threading.Lock] = {}

    def history(self, deal_id: str) -> list:
        return self.sessions.setdefault(deal_id, [])

    def chat(self, deal_id: str, user_text: str) -> Reply:
        self.s.crm.get_deal(deal_id)  # raises DealNotFound before we spend tokens
        lock = self._locks.setdefault(deal_id, threading.Lock())
        with lock:  # one turn at a time per deal keeps the history consistent
            return self._turn(deal_id, user_text)

    def _turn(self, deal_id: str, user_text: str) -> Reply:
        messages = self.history(deal_id)
        tools = DealTools(self.s, deal_id)
        messages.append({"role": "user", "content": user_text})
        calls: list[dict] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}

        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.beta.messages.create(
                model=self.s.settings.model,
                max_tokens=16000,
                system=self.s.system_prompt,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": self.s.settings.effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            _add_usage(usage, response)

            if response.stop_reason == "refusal":
                # Drop the unanswered user turn so the history stays a clean prefix, then hand off.
                messages.pop()
                category = getattr(response.stop_details, "category", None)
                self.s.crm.create_task(deal_id, "Closing agent declined a message",
                                       f"Category: {category}. Buyer wrote: {user_text[:500]}")
                return Reply(HANDOFF_TEXT, calls, "refusal", usage)

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                text = "\n\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    log.warning("deal %s: reply hit max_tokens", deal_id)
                return Reply(text, calls, response.stop_reason, usage)

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                try:
                    output, is_error = tools.run(block.name, args), False
                except ToolError as exc:
                    output, is_error = f"Error: {exc}", True
                calls.append({"tool": block.name, "input": args, "is_error": is_error})
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})  # all results in ONE message

        self.s.crm.create_task(deal_id, "Closing agent stopped: too many tool calls",
                               f"Last buyer message: {user_text[:500]}")
        return Reply(HANDOFF_TEXT, calls, "tool_budget_exceeded", usage)


def _add_usage(total: dict, response) -> None:
    u = getattr(response, "usage", None)
    if not u:
        return
    for key in total:
        total[key] += getattr(u, key, 0) or 0
