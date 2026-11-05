"""Chat with the Feasibility Bot in a terminal: ``pipeline-bot``."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from pipeline_analytics.bot.agent import FeasibilityBot
    except ImportError:  # pragma: no cover
        print("Install the bot extra: uv sync --extra bot", file=sys.stderr)
        return 1
    try:
        bot = FeasibilityBot(on_tool=lambda name, _: print(f"  [running {name}]", flush=True))
    except Exception as e:  # pragma: no cover - credential problems surface here
        print(f"Could not start the Claude client: {e}", file=sys.stderr)
        return 1
    print("Pipeline feasibility bot. Describe your project; 'exit' to quit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if text.lower() in {"exit", "quit"}:
            return 0
        if not text:
            continue
        try:
            reply = bot.ask(text)
        except Exception as e:  # pragma: no cover - credentials, network, rate limits
            print(f"Claude API call failed: {e}\nSet ANTHROPIC_API_KEY or run `ant auth login`.")
            continue
        print(f"\nbot> {reply.text}\n")
        if reply.unsupported:
            print(
                f"  [check: figures not found in any tool result: {', '.join(reply.unsupported)}]\n"
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
