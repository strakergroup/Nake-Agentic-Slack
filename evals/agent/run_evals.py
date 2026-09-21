"""Run the scripted conversations against real Claude with pretend tools.

    ../agent-env/bin/python evals/agent/run_evals.py --limit 5     # pilot: measure cost first
    ../agent-env/bin/python evals/agent/run_evals.py               # all conversations
    ../agent-env/bin/python evals/agent/run_evals.py --dry-run     # check the harness, no API calls

Every real run spends money. Needs ANTHROPIC_API_KEY (or --env-file). Uses only the
agent core: no Slack, no database, none of the app's infrastructure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.agent.core import copy  # noqa: E402
from app.agent.core.approvals import ApprovalGate  # noqa: E402
from app.agent.core.audit import ListAuditSink  # noqa: E402
from app.agent.core.llm import ClaudeLlm, LlmStep  # noqa: E402
from app.agent.core.runner import AgentRunner  # noqa: E402
from app.agent.core.session import InMemorySessionStore  # noqa: E402
from app.agent.core.tools import ToolRegistry  # noqa: E402
from app.agent.core.types import AgentFacts, ToolOutcome, session_key  # noqa: E402

PRICE_IN, PRICE_OUT = 5.00, 25.00  # USD per million tokens, claude-opus-5 list price

JOBS = [
    {
        "id": "TJ48213",
        "status": "IN_PROGRESS",
        "source_language": "English",
        "target_languages": ["Japanese", "German"],
        "target_date": "2026-09-22",
    },
    {
        "id": "TJ48190",
        "status": "PENDING_QUOTES",
        "source_language": "English",
        "target_languages": ["French"],
        "target_date": "",
    },
]

_LANGUAGE_HINTS = {
    "ja": re.compile(r"[぀-ヿ一-鿿]"),
    "de": re.compile(r"\b(und|ich|kann|für|dich|deine|oder|nicht|ist)\b", re.I),
    "pt": re.compile(r"\b(você|posso|para|seus|uma|não|com|traduzir)\b", re.I),
    "fr": re.compile(r"\b(vous|je|peux|pour|des|vos|une|traduire|et)\b", re.I),
    "en": re.compile(r"\b(the|and|your|can|you)\b", re.I),
}


class Recorder:
    def __init__(self):
        self.replies: list[str] = []

    async def set_status(self, facts, status, title=None): ...
    async def stream_start(self, facts, text):
        return "ts"

    async def stream_tasks(self, facts, ts, plan): ...
    async def stream_stop(self, facts, ts, text, blocks, session_status):
        self.replies.append(text)

    async def post(self, facts, text, blocks=None):
        self.replies.append(text)

    async def post_private(self, facts, text, blocks=None):
        self.replies.append(text)


class DryRunLlm:
    """Stands in for Claude so the harness itself can be checked for free."""

    async def step(self, system, tools, messages):
        return LlmStep(
            [{"type": "text", "text": "Dry run."}],
            [],
            "Dry run.",
            "end_turn",
            0,
            0,
            "dry-run",
        )


def build(case, llm):
    facts = AgentFacts(
        **{
            **dict(
                user_id="U1",
                team_id="T1",
                channel_id="D1",
                thread_ts="1.1",
                is_connected=True,
                can_see_quotes=True,
                display_name="Mika Kato",
            ),
            **case.get("facts", {}),
        }
    )
    calls: list[tuple[str, dict]] = []
    tools = ToolRegistry(native_document_quotes=bool(case.get("native_quotes", False)))

    def handler(name, content):
        async def run(_facts, tool_input):
            calls.append((name, tool_input))
            return ToolOutcome(content=content)

        return run

    tools.bind("get_job", handler("get_job", json.dumps(JOBS[:1])))
    tools.bind("list_jobs", handler("list_jobs", json.dumps(JOBS)))
    tools.bind("account_status", handler("account_status", "ready"))
    tools.bind(
        "translate_text",
        handler(
            "translate_text",
            "Requested. The translation service will post the result shortly.",
        ),
    )
    tools.bind(
        "request_document_quote",
        handler(
            "request_document_quote",
            "Quote requested. It will arrive as its own message with an Accept button.",
        ),
    )
    tools.bind(
        "post_translation_publicly", handler("post_translation_publicly", "posted")
    )
    tools.bind(
        "offer_form",
        handler("offer_form", "A button that opens the form has been shown."),
    )
    tools.bind("set_digest", handler("set_digest", copy.DIGEST_ON))
    tools.bind(
        "explain",
        handler(
            "explain", copy.HELP_IBM_GENERIC if facts.is_ibm else copy.HELP_GENERAL
        ),
    )

    slack, store, audit = Recorder(), InMemorySessionStore(), ListAuditSink()
    runner = AgentRunner(
        llm,
        slack,
        store,
        tools,
        ApprovalGate(time.time, is_gated=tools.is_gated),
        time.time,
        audit,
    )
    return facts, runner, slack, store, audit, calls


def check(case, calls, replies, pending, dry_run):
    problems = []
    names = [n for n, _ in calls]
    if not dry_run:
        position = 0
        for wanted in case.get("expect_tools", []):
            if wanted in names[position:]:
                position = names.index(wanted, position) + 1
            else:
                problems.append(f"expected tool {wanted}; called {names}")
        if "expect_form" in case:
            forms = [i.get("form") for n, i in calls if n == "offer_form"]
            if case["expect_form"] not in forms:
                problems.append(f"expected form {case['expect_form']}; offered {forms}")
        if case.get("expect_pending") and not pending:
            problems.append("expected an approval to be waiting")
        language = case.get("reply_language")
        if language and not _LANGUAGE_HINTS[language].search(" ".join(replies)):
            problems.append(f"reply does not look like {language}")
    for banned in case.get("forbid_tools", []):
        if banned in names:
            problems.append(f"forbidden tool called: {banned}")
    for gated in ("post_translation_publicly", "submit_document_translation"):
        if gated in names:
            problems.append(f"SAFETY: gated tool {gated} ran without a click")
    for pattern in case.get("forbid_text", []):
        for reply in replies:
            if re.search(pattern, reply, re.I):
                problems.append(f"forbidden text /{pattern}/ in reply")
    return problems


async def run_case(case, llm, dry_run):
    facts, runner, slack, store, audit, calls = build(case, llm)
    for index, text in enumerate(case["turns"]):
        files = case.get("files") if index == 0 else None
        await runner.handle_message(facts, text, f"{case['id']}-{index}", files)
    session = await store.load(session_key(facts))
    pending = bool(session and session.pending)
    tokens_in = sum(t.input_tokens for t in audit.turns)
    tokens_out = sum(t.output_tokens for t in audit.turns)
    return {
        "id": case["id"],
        "problems": check(case, calls, slack.replies, pending, dry_run),
        "tools": [n for n, _ in calls],
        "replies": slack.replies,
        "cost": tokens_in / 1e6 * PRICE_IN + tokens_out / 1e6 * PRICE_OUT,
        "tokens": (tokens_in, tokens_out),
        "outcomes": [t.outcome for t in audit.turns],
    }


def load_env(path):
    if path and pathlib.Path(path).exists():
        for line in pathlib.Path(path).read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--env-file", default=str(ROOT.parent / ".env"))
    parser.add_argument("--model", default="claude-opus-5")
    args = parser.parse_args()

    cases = yaml.safe_load(
        (pathlib.Path(__file__).parent / "conversations.yaml").read_text()
    )
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    if args.limit:
        cases = cases[: args.limit]

    if args.dry_run:
        llm = DryRunLlm()
    else:
        load_env(args.env_file)
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit(
                "No ANTHROPIC_API_KEY found. Add it to the .env file beside the clone, or use --dry-run."
            )
        llm = ClaudeLlm(model=args.model)

    results = []
    for case in cases:
        result = await run_case(case, llm, args.dry_run)
        results.append(result)
        status = "PASS" if not result["problems"] else "FAIL"
        print(
            f"{status}  {result['id']:<40} tools={result['tools']} cost=${result['cost']:.4f}"
        )
        for problem in result["problems"]:
            print(f"        {problem}")

    passed = sum(1 for r in results if not r["problems"])
    total_cost = sum(r["cost"] for r in results)
    worst = max((r["cost"] for r in results), default=0)
    print(
        f"\n{passed}/{len(results)} passed. Total ${total_cost:.2f}, mean ${total_cost / max(1, len(results)):.4f}, worst ${worst:.4f} per conversation."
    )

    if not args.dry_run:
        lines = [
            f"# Arbitr agent evals ({args.model})",
            "",
            f"{passed}/{len(results)} passed. Total ${total_cost:.2f}, "
            f"mean ${total_cost / max(1, len(results)):.4f}, worst ${worst:.4f} per conversation.",
            "",
        ]
        for r in results:
            lines += [
                f"## {'PASS' if not r['problems'] else 'FAIL'}: {r['id']}",
                f"- tools: {r['tools']}",
                f"- tokens in/out: {r['tokens'][0]}/{r['tokens'][1]}, cost ${r['cost']:.4f}",
                *[f"- problem: {p}" for p in r["problems"]],
                "",
                *[f"> {line}" for reply in r["replies"] for line in reply.splitlines()],
                "",
            ]
        (pathlib.Path(__file__).parent / "report.md").write_text("\n".join(lines))
        print("Wrote evals/agent/report.md")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
