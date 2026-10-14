"""Live run against the Claude API on a throw-away copy of the demo data.

Usage:  python scripts/live_demo.py [deal_id]
Needs ANTHROPIC_API_KEY (or another credential the SDK can resolve). Costs a few cents.
Writes the transcript to docs/live_run.md.
"""

import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from closing_agent.agent import ClosingAgent  # noqa: E402
from closing_agent.services import Services, Settings  # noqa: E402

CONVERSATION = [
    "Hi, what do I still need to do to finish our contract?",
    "Could you knock 10% off? Our second practice might join next year.",
    "OK. What would it cost if we went for 24 months instead of 12?",
    "Let's do that - 12 seats, 24 months. Please send me a reminder with what's missing.",
]


def main() -> None:
    deal_id = sys.argv[1] if len(sys.argv) > 1 else "D-1002"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        shutil.copytree(ROOT / "config", tmp / "config")
        shutil.copytree(ROOT / "data", tmp / "data",
                        ignore=shutil.ignore_patterns("state.sqlite", "outbox.jsonl", "uploads"))
        services = Services(Settings.load(tmp / "config" / "settings.yaml", root=tmp))
        agent = ClosingAgent(services)
        out = [f"# Live run {date.today()} - deal {deal_id}", "",
               f"Model `{services.settings.model}`, effort `{services.settings.effort}`. "
               "Unedited output of `scripts/live_demo.py`.", ""]
        totals: dict[str, int] = {}
        for msg in CONVERSATION:
            reply = agent.chat(deal_id, msg)
            tools = ", ".join(c["tool"] + (" (error)" if c["is_error"] else "") for c in reply.tool_calls)
            out += [f"**Buyer:** {msg}", "", f"*Tools: {tools or '-'}*", "", "**Agent:**", "",
                    reply.text, "", "---", ""]
            for k, v in reply.usage.items():
                totals[k] = totals.get(k, 0) + v
        out += [f"- CRM stage: `{services.crm.get_deal(deal_id)['stage']}`",
                f"- CRM tasks: {[t['title'] for t in services.crm.tasks(deal_id)]}",
                f"- Reminders sent: {len(services.nudges.status(deal_id)['sent'])}",
                f"- Token usage: {totals}"]
        services.nudges.close()
        text = "\n".join(out) + "\n"
        (ROOT / "docs" / "live_run.md").write_text(text, encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
