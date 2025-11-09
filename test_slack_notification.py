#!/usr/bin/env python3
"""Test script to send a real exception notification to Slack.

This script tests the _send_to_slack_background function by creating
a test exception and sending it to the configured Slack dev alert channel.
"""

import asyncio
import sys
from pathlib import Path

# Add the app directory to the path so we can import modules
sys.path.insert(0, str(Path(__file__).parent))

from app.slack.utils import _send_to_slack_background


class TestException(Exception):
    """Test exception for Slack notification testing."""

    pass


async def main():
    """Create a test exception and send it to Slack."""
    print("Creating test exception...")

    try:
        # Create a test exception with a traceback
        raise TestException(
            "This is a test exception to verify Slack notifications are working!"
        )
    except TestException as e:
        print(f"Exception created: {type(e).__name__}: {e}")
        print("\nSending exception notification to Slack...")

        try:
            await _send_to_slack_background(e)
            print("✅ Successfully sent exception notification to Slack!")
        except Exception as send_error:
            print(f"❌ Failed to send notification: {send_error}")
            import traceback

            traceback.print_exc()
            return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
