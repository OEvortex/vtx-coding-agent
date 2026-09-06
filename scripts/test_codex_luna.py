#!/usr/bin/env python3
"""Programmatically test the Codex OAuth provider in VTX.

Tries Luna first, then falls back to other known Codex models to verify
the provider plumbing works end-to-end.

Run:
    uv run python scripts/test_codex_luna.py
"""

from __future__ import annotations

import asyncio
import sys

from vtx.ai import ProviderConfig, get_valid_codex_token_sync
from vtx.ai.oauth.codex import is_codex_logged_in, login_with_device_code
from vtx.ai.providers.openai_responses_sdk import OpenAIResponsesSDKProvider
from vtx.core.types import UserMessage


MODELS_TO_TRY = [
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.2",
    "gpt-5.2-mini",
    "gpt-daybreak-blue-latest",
    "gpt-daybreak-red-latest",
    "gpt-5.5-codex",
    "codex-auto-review",
]
TEST_MESSAGE = "Say hello in exactly 3 words. No punctuation."


def check_existing_auth() -> bool:
    print("[auth] Checking existing Codex credentials...")
    if is_codex_logged_in():
        token = get_valid_codex_token_sync()
        if token:
            print(f"[auth] Found valid saved token (starts with {token[:10]}...).")
            return True
    print("[auth] No valid saved Codex credentials found.")
    return False


async def ensure_authenticated() -> None:
    if check_existing_auth():
        return

    print("[auth] Starting device-code login flow...")
    print("[auth] You will see a URL and a user code below.")

    def on_user_code(verification_url: str, user_code: str) -> None:
        print(f"\n{'=' * 60}")
        print(f"  OPEN THIS URL: {verification_url}")
        print(f"  ENTER THIS CODE: {user_code}")
        print(f"{'=' * 60}\n")

    try:
        creds = await login_with_device_code(on_user_code=on_user_code)
    except Exception as exc:
        print(f"[auth] Login failed: {exc}")
        sys.exit(1)

    print(f"[auth] Login successful!")
    print(f"       account_id: {creds.account_id}")
    print(f"       access_token starts with: {creds.access[:10]}...")
    print(f"       refresh_token starts with: {creds.refresh[:10]}...")


async def test_model(model: str) -> tuple[str, str | None, str | None, str | None]:
    config = ProviderConfig(
        model=model,
        provider="codex",
        base_url="https://chatgpt.com/backend-api/codex",
        max_tokens=None,
    )
    provider = OpenAIResponsesSDKProvider(config)

    stream = await provider.stream(messages=[UserMessage(content=TEST_MESSAGE)])

    content_parts = []
    thinking_parts = []
    usage_info = None
    finish_reason = "stop"
    stream_error = None

    async for part in stream:
        from vtx.core.types import StreamPart, TextPart, ThinkPart, StreamDone, StreamError

        if isinstance(part, StreamError):
            stream_error = part.error
            break
        if isinstance(part, TextPart):
            content_parts.append(part.text)
        elif isinstance(part, ThinkPart):
            thinking_parts.append(part.think)
        elif isinstance(part, StreamDone):
            finish_reason = part.stop_reason

    if stream.usage:
        usage_info = {
            "prompt_tokens": stream.usage.input_tokens,
            "completion_tokens": stream.usage.output_tokens,
            "total_tokens": stream.usage.input_tokens + stream.usage.output_tokens,
        }

    content = "".join(content_parts) if content_parts else None
    reasoning = "\n".join(thinking_parts) if thinking_parts else None

    return model, content, finish_reason, stream_error


async def main() -> None:
    await ensure_authenticated()

    print(f"\n[api] Testing Codex provider with Luna model first, then fallbacks...")
    print(f"[api] Test message: {TEST_MESSAGE!r}\n")

    for model in MODELS_TO_TRY:
        print(f"[api] Trying model={model!r}...")
        try:
            used_model, content, finish_reason, stream_error = await test_model(model)
        except Exception as exc:
            print(f"       FAILED: {exc}\n")
            continue

        if stream_error:
            print(f"       STREAM ERROR: {stream_error}\n")
            continue

        if content:
            print(f"       SUCCESS: {content!r}")
            print(f"       finish_reason={finish_reason}")
            if used_model == "gpt-5.6-luna":
                print(f"\n[result] SUCCESS: Luna model responded via Codex provider.")
            else:
                print(
                    f"\n[result] Luna is gated; {used_model} works. Provider plumbing is healthy."
                )
            return
        else:
            print(f"       EMPTY RESPONSE (finish_reason={finish_reason})\n")

    print("\n[result] All models returned empty or failed. Check account/model access.")


if __name__ == "__main__":
    asyncio.run(main())
