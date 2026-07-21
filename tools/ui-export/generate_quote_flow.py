"""Generate focused Block Kit JSON for the IBM HT staged quote flow."""

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
        "title": "1. PDF quote before job creation",
        "entry_name": "EvaluationCreditsQuoteMessage (IBM HT PDF prequote estimate)",
        "note": (
            "SRT shows AI Translation + PDF Conversion cost before Adobe "
            "conversion or CVA job creation."
        ),
    },
    {
        "title": "2. Hidden AI quote auto-proceeds",
        "entry_name": "EvaluationCreditsQuoteMessage (IBM HT AI quote with PDF)",
        "note": (
            "After accept, ISVC converts the PDF, CVA creates the job, and SRT "
            "auto-proceeds the accurate AI quote using dollar display for IBM."
        ),
    },
    {
        "title": "3. Combined QE and Human Translation quote",
        "entry_name": "HumanJobQuoteMessage",
        "note": (
            "After MT completes, Slack updates the original message with one quote "
            "for Quality Evaluation plus the worst-case Human Translation discount."
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
        "generated_by": "tools/ui-export/generate_quote_flow.py",
        "title": "IBM HT Staged Quote Flow",
        "steps": build_flow_entries(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate focused Block Kit JSON for staged quote flow."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "output" / "ibm-ht-staged-quote-flow.json",
        help="Path to write the generated JSON file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_flow_payload()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Generated {len(payload['steps'])} quote flow steps -> {args.output}")


if __name__ == "__main__":
    main()
