"""Generate focused Block Kit JSON for the direct AI Translate quote flow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_blocks import build_all_messages, configure_translation

FLOW_STEPS = [
    {
        "title": "1. AI Translate quote",
        "entry_name": "DocumentMtQuoteMessage (IBM AI Translate direct quote)",
        "note": (
            "The direct Document MT flow posts an AI Translate quote with an "
            "Accept Quote button before publishing the real MT job."
        ),
    },
    {
        "title": "2. AI Translate quote accepted",
        "entry_name": "DocumentMtQuoteMessage (IBM AI Translate accepted)",
        "note": (
            "After accept, the original quote message is updated to remove the "
            "action buttons while the MT job is published."
        ),
    },
]


def build_flow_entries() -> list[dict[str, Any]]:
    configure_translation("en")
    messages_by_name = {entry["name"]: entry for entry in build_all_messages()}
    return [
        {
            **step,
            "type": messages_by_name[step["entry_name"]]["type"],
            "blocks": messages_by_name[step["entry_name"]]["blocks"],
        }
        for step in FLOW_STEPS
    ]


def build_flow_payload() -> dict[str, Any]:
    return {
        "generated_by": "tools/ui-export/generate_ai_translate_quote_flow.py",
        "title": "Direct AI Translate Quote Flow",
        "subtitle": (
            "Focused static mock of the direct AI Translate quote confirmation "
            "messages, rendered with the same Slack Block Kit renderer and styles as the full UI catalog."
        ),
        "steps": build_flow_entries(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate focused Block Kit JSON for the direct AI Translate quote flow."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "output" / "ai-translate-quote-flow.json",
        help="Path to write the generated JSON file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_flow_payload()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"Generated {len(payload['steps'])} AI Translate quote steps -> {args.output}"
    )


if __name__ == "__main__":
    main()
