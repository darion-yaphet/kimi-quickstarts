"""Run Kimi Code with ordinary computer tools backed by the example VNC driver.

Usage:

    python run.py "Open a terminal, run date, and tell me today's date and time zone."

It builds the example driver, exposes its actions as ordinary custom tools, and prints each model response until the
model finishes. Before each action other than a screenshot, it shows you the call and asks y/N.

It requires `pip install -r requirements.txt`, `vnc_computer.py` next to this file, a VNC server without a password at
`VNC_HOST:VNC_PORT` (default `127.0.0.1:5900`) and a Kimi Code API key in `ANTHROPIC_API_KEY`.

Read "Running a computer toolset safely" in the SDK guide (computer-toolset.md) before running this against anything
other than a throwaway desktop inside a sandbox, because what is on the screen steers what the model does next.

The environment variables are `VNC_HOST` and `VNC_PORT` for the server, plus `ANTHROPIC_BASE_URL`, `MODEL`, and
`MAX_TOOL_TURNS` for Kimi.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from anthropic import Anthropic, AnthropicError
from anthropic.tools.computer import BetaComputerConfirmContext

from kimi_adapter import run_loop
from vnc_computer import VncComputer


def ask(context: BetaComputerConfirmContext) -> bool:
    """Show the person at the terminal the tool call, and run it only if they answer y."""
    if context.member == "screenshot":  # A screenshot changes nothing on the desktop, so it runs unasked.
        return True
    call = json.dumps(context.input.model_dump(exclude_none=True))  # json.dumps escapes invisible characters.
    return input(f"Allow {context.member} {call}? [y/N] ").strip().lower() == "y"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the computer toolset on a task with the example driver.")
    parser.add_argument("task", nargs="*", help="what the model should do (default: read the date)")
    args = parser.parse_args()
    task = " ".join(args.task) or "Open a terminal, run date, and tell me today's date and time zone."
    try:
        client = Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
            base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.kimi.com/coding/"),
        )
        computer = VncComputer(
            host=os.environ.get("VNC_HOST", "127.0.0.1"), port=int(os.environ.get("VNC_PORT", "5900")), confirm=ask
        )
    except (AnthropicError, OSError, RuntimeError) as error:  # There is no SDK profile, or no usable VNC server.
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)
    with computer:
        run_loop(
            client,
            computer,
            task,
            model=os.environ.get("MODEL", "kimi-for-coding"),
            max_turns=int(os.environ.get("MAX_TOOL_TURNS", "30")),
        )


if __name__ == "__main__":
    main()
