#!/usr/bin/env python3
"""Supercode CLI — Send a prompt to Supercode and stream the response.

Reads OAuth token from ``~/.better-auth/token.json`` (written by
``supercode login``) — no ``SUPERCODE_TOKEN`` env var fallback.

Usage:
    python scripts/supercode_proxy.py "What is the weather in NYC?"
    python scripts/supercode_proxy.py --model google/gemini-2.5-flash "Hello"
    python scripts/supercode_proxy.py --model google/gemini-2.5-flash \\
        --system "You are a helpful assistant" "Hi"
    python scripts/supercode_proxy.py --show-usage "Tell me a joke"
    python scripts/supercode_proxy.py --raw "Hi"   # Show raw NDJSON events
    python scripts/supercode_proxy.py --help
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncGenerator
from typing import Any

import httpx

SUPERCODE_URL = os.environ.get("SUPERCODE_URL", "https://supercode-8w7e.onrender.com")


# ── Auth ────────────────────────────────────────────────────────────────────────


def _get_token() -> str:
    """Load the OAuth token from ``~/.better-auth/token.json``."""
    path = os.path.expanduser("~/.better-auth/token.json")
    try:
        with open(path) as f:
            data = json.load(f)
        token = data.get("access_token", "")
        if token:
            return token
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    raise RuntimeError("No Supercode token found. Run `supercode login` first.")


# ── API call ────────────────────────────────────────────────────────────────────


async def _stream_ndjson(payload: dict[str, Any]) -> AsyncGenerator[dict[str, Any]]:
    """Call Supercode and yield parsed NDJSON events."""
    token = _get_token()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{SUPERCODE_URL}/api/ai/chat", json=payload, headers=headers)
        if resp.status_code >= 400:
            body = await resp.aread()
            try:
                err = json.loads(body)
                msg = err.get("error", body.decode())
            except Exception:
                msg = body.decode()
            raise RuntimeError(f"Supercode API error {resp.status_code}: {msg}")

        async for line in resp.aiter_lines():
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


# ── Event handlers ──────────────────────────────────────────────────────────────


def _handle_text(event: dict[str, Any], *, end: str = "") -> None:
    sys.stdout.write(event.get("content", ""))
    sys.stdout.flush()


def _handle_tool_call(event: dict[str, Any]) -> None:
    tc_id = event.get("toolCallId", "?")
    tc_name = event.get("toolName", "?")
    tc_args = json.dumps(event.get("args", {}))
    sys.stdout.write(f"\n  ▶ Tool call: {tc_name}({tc_args}) [{tc_id}]\n")
    sys.stdout.flush()


def _handle_reasoning(event: dict[str, Any]) -> None:
    content = event.get("content", "")
    if content:
        sys.stdout.write(f"\n  🧠 {content}\n")
        sys.stdout.flush()


def _handle_finish(event: dict[str, Any], *, show_usage: bool = False) -> str:
    reason = event.get("reason", "stop")
    usage_raw = event.get("usage", {}) or {}
    if show_usage:
        print("\n  ── Usage ──")
        print(f"    Input tokens:  {usage_raw.get('inputTokens', 0)}")
        print(f"    Output tokens: {usage_raw.get('outputTokens', 0)}")
        input_detail = usage_raw.get("inputTokenDetails") or {}
        if input_detail:
            print(f"    Cache read:    {input_detail.get('cacheReadTokens', 0)}")
            print(f"    Cache write:   {input_detail.get('cacheWriteTokens', 0)}")
        output_detail = usage_raw.get("outputTokenDetails") or {}
        if output_detail:
            print(f"    Reasoning:     {output_detail.get('reasoningTokens', 0)}")
    return reason


# ── Main ────────────────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a prompt to Supercode API and stream the response.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("prompt", nargs="*", help="The user message to send")
    parser.add_argument(
        "--model",
        default="concentrateai/deepseek-v4-flash",
        help="Model ID in subprovider/modelname format (default: concentrateai/deepseek-v4-flash)",
    )
    parser.add_argument("--system", default="", help="Optional system prompt")
    parser.add_argument(
        "--show-usage", action="store_true", help="Print token usage after the response"
    )
    parser.add_argument(
        "--raw", action="store_true", help="Show raw NDJSON events instead of formatted output"
    )
    args = parser.parse_args()

    prompt = " ".join(args.prompt) if args.prompt else None
    if not prompt and sys.stdin.isatty():
        parser.print_help()
        sys.exit(1)
    if not prompt:
        prompt = sys.stdin.read().strip()

    # Build payload
    provider, model_name = (
        args.model.split("/", 1) if "/" in args.model else ("concentrateai", args.model)
    )
    messages: list[dict[str, Any]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": prompt})

    payload = {"provider": provider, "model": model_name, "messages": messages}

    # Verify token
    try:
        token_preview = _get_token()[:16]
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Model:     {args.model}", file=sys.stderr)
    print(f"Token:     {token_preview}...", file=sys.stderr)
    print(file=sys.stderr)

    has_tool_calls = False
    finish_reason = "stop"

    try:
        async for event in _stream_ndjson(payload):
            if args.raw:
                print(json.dumps(event))
                sys.stdout.flush()
                continue

            match event.get("type"):
                case "text":
                    _handle_text(event)
                case "reasoning":
                    _handle_reasoning(event)
                case "tool-call":
                    has_tool_calls = True
                    _handle_tool_call(event)
                case "finish":
                    finish_reason = _handle_finish(event, show_usage=args.show_usage)
                case "error":
                    print(f"\nERROR: {event.get('error', 'Unknown error')}", file=sys.stderr)
                    sys.exit(1)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(file=sys.stderr)
    if has_tool_calls:
        print("\nFinish reason: tool_calls (forced — API always sends 'stop')", file=sys.stderr)
    else:
        print(f"\nFinish reason: {finish_reason}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
