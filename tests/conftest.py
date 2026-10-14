import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("CLOSING_NO_AUTOAPP", "1")

from closing_agent.services import Services, Settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def services(tmp_path, monkeypatch) -> Services:
    """Isolated copy of config + sample data, so tests can mutate the CRM freely."""
    shutil.copytree(ROOT / "config", tmp_path / "config")
    shutil.copytree(ROOT / "data", tmp_path / "data",
                    ignore=shutil.ignore_patterns("state.sqlite", "outbox.jsonl", "uploads"))
    monkeypatch.setenv("CLOSING_WIDGET_SECRET", "test-secret")
    monkeypatch.setenv("CLOSING_ADMIN_KEY", "admin-test")
    return Services(Settings.load(tmp_path / "config" / "settings.yaml", root=tmp_path))


# ---------- a scripted stand-in for the Anthropic client ----------

def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_block(name, args, block_id):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=block_id)


def response(blocks, stop_reason, **extra):
    usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0)
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=usage,
                           stop_details=extra.get("stop_details"))


class FakeClient:
    """Replays queued responses and records every request."""

    def __init__(self, responses):
        self.queue = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # snapshot: the agent keeps appending to the same list after the call
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.queue.pop(0)
