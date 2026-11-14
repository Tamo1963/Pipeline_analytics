"""Chat with the Feasibility Bot in a terminal: ``pipeline-bot``.

Works with any provider (see ``bot/providers.py``)::

    pipeline-bot --provider openrouter --api-key sk-or-... --list-models
    pipeline-bot --provider openrouter --api-key sk-or-... --model openai/gpt-5
    pipeline-bot --provider ollama --model llama3.1
    pipeline-bot --provider custom --base-url https://llm.example.com/v1 --api-key ... --model m

Flags override the PIPELINE_BOT_* environment variables.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline-bot", description="Pipeline feasibility bot")
    ap.add_argument("--provider", help="openrouter, groq, openai, ollama, anthropic, custom, ...")
    ap.add_argument("--model", help="model id")
    ap.add_argument("--base-url", help="override the provider's endpoint")
    ap.add_argument("--api-key", help="the provider's API key (else from the environment)")
    ap.add_argument("--list-models", action="store_true", help="list the key's models and exit")
    a = ap.parse_args(argv)
    try:
        from pipeline_analytics.bot.agent import FeasibilityBot
        from pipeline_analytics.bot.providers import (
            env_key,
            env_settings,
            list_models,
            make_provider,
            preset_named,
        )
    except ImportError:  # pragma: no cover
        print("Install the bot extra: uv sync --extra bot", file=sys.stderr)
        return 1
    try:
        if a.provider:
            preset = preset_named(a.provider)
            if preset is None:
                print(f"Unknown provider {a.provider!r}.", file=sys.stderr)
                return 2
            base = a.base_url or os.environ.get("PIPELINE_BOT_BASE_URL", "") or preset.base_url
            key = a.api_key or os.environ.get("PIPELINE_BOT_API_KEY", "") or env_key(preset)
            model = a.model or os.environ.get("PIPELINE_BOT_MODEL", "")
        else:
            st = env_settings()
            if st is None:
                print("No LLM provider configured. Use --provider and --api-key (and --model), "
                      "or set PIPELINE_BOT_PROVIDER.", file=sys.stderr)  # fmt: skip
                return 2
            preset, model, base, key = st
            base, key, model = a.base_url or base, a.api_key or key, a.model or model
        if a.list_models:
            for m in list_models(preset.kind, base, key):
                print(m)
            return 0
        if preset.needs_key and not key:
            print(f"{preset.name} needs an API key (--api-key or {preset.key_env}).",
                  file=sys.stderr)  # fmt: skip
            return 2
        if not model:
            print("Choose a model with --model (see --list-models).", file=sys.stderr)
            return 2
        provider = make_provider(preset.kind, model=model, base_url=base, api_key=key)
        bot = FeasibilityBot(
            provider=provider, on_tool=lambda name, _: print(f"  [running {name}]", flush=True)
        )
    except Exception as e:  # bad settings, rejected key, unreachable endpoint
        print(f"Could not start the LLM client: {e}", file=sys.stderr)
        return 1
    print(f"Pipeline feasibility bot [{preset.name}: {bot.model}]. "
          "Describe your project; 'exit' to quit.\n")  # fmt: skip
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
            print(f"Model call failed: {e}\nCheck the provider, model and API key.")
            continue
        print(f"\nbot> {reply.text}\n")
        if reply.unsupported:
            print(
                f"  [check: figures not found in any tool result: {', '.join(reply.unsupported)}]\n"
            )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
