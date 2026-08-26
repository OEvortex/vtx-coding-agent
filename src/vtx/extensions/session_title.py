"""Auto-generate a concise session title from the first user prompt.

Listens for ``input`` and ``agent_settled`` events. On the first real user
message in a session it stores the text, then after the agent finishes it
asks the current model to produce a short title and persists it via
``api.set_session_name()``.

Configuration is read from ``vtx_config.extensions.session_title``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from vtx.ai.agent.extensions import AGENT_SETTLED, INPUT
from vtx.ai.base import ProviderConfig, get_env_api_key, resolve_api_key
from vtx.coding_agent.config import config as vtx_config
from vtx.coding_agent.runtime import create_provider

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_INPUT_CHARS = 2000
DEFAULT_MAX_TOKENS = 40
DEFAULT_MAX_TITLE_LENGTH = 48
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_THINKING_LEVEL = "minimal"

_TITLE_SYSTEM_PROMPT = (
    "Generate a concise session title for a coding-agent conversation.\n"
    "\n"
    "Rules:\n"
    "- Use the same language as the first user message.\n"
    "- Return exactly one line of plain text and nothing else.\n"
    "- Describe the specific task; avoid generic labels such as "
    '"code changes" or "problem solving".\n'
    "- Do not use Markdown, quotation marks, or trailing punctuation.\n"
    "- Keep the title within the requested character limit.\n"
)

_ANSI_PATTERN = re.compile(
    r"[\u001b\u009b](?:\[[0-?]*[ -/]*[@-~]|\][^\u0007]*(?:\u0007|\u001b\\)?)"
)
_CONTROL_PATTERN = re.compile(r"[\u0000-\u0009\u000b-\u001f\u007f-\u009f]")
_TRAILING_PUNCTUATION = re.compile(r"[.!?,;:\uFF0C\u3002\uFF01\uFF1F\uFF1B\uFF1A\u300C\u300D]+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_title(value: str, max_length: int = DEFAULT_MAX_TITLE_LENGTH) -> str | None:
    title = value.strip()
    if not title:
        return None

    # Strip ANSI escape sequences and control characters.
    title = _ANSI_PATTERN.sub("", title)
    title = _CONTROL_PATTERN.sub("", title)

    # Take the first non-empty line.
    lines = [line.strip() for line in title.splitlines() if line.strip()]
    if not lines:
        return None
    title = lines[0]

    # Remove common Markdown wrappers.
    title = re.sub(r"^#{1,6}\s+", "", title)
    title = re.sub(r"^[-*+]\s+", "", title)
    title = re.sub(r"^\*\*|__|~~|`", "", title)
    title = re.sub(r"\*\*|__|~~|`$", "", title)
    title = re.sub(r'^["\'""' '«»]|["\'""' "«»]$", "", title)
    title = title.strip()

    # Remove trailing punctuation.
    title = _TRAILING_PUNCTUATION.sub("", title).strip()

    # Truncate to max_length Unicode code points.
    if len(title) > max_length:
        title = title[:max_length].rstrip()
        title = _TRAILING_PUNCTUATION.sub("", title).strip()

    return title or None


def _is_command(text: str) -> bool:
    return text.startswith("/") or text.startswith("!") or text.startswith("$")


def _get_extension_config() -> dict[str, Any]:
    section = getattr(vtx_config, "extensions", None)
    if section is None:
        return {}
    return getattr(section, "session_title", {}) or {}


# ---------------------------------------------------------------------------
# Extension
# ---------------------------------------------------------------------------


def register(api: Any) -> None:
    config = _get_extension_config()
    if not config.get("enabled", True):
        return

    max_input_chars = int(config.get("max_input_chars", DEFAULT_MAX_INPUT_CHARS))
    max_tokens = int(config.get("max_tokens", DEFAULT_MAX_TOKENS))
    max_title_length = int(config.get("max_title_length", DEFAULT_MAX_TITLE_LENGTH))
    timeout_seconds = int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    thinking_level = str(config.get("thinking_level", DEFAULT_THINKING_LEVEL))

    state: dict[str, Any] = {
        "first_user_text": None,
        "title_generated": False,
        "generating": False,
        "_futures": set(),
    }

    async def _generate_title(first_text: str) -> None:
        if state["generating"]:
            return
        state["generating"] = True
        try:
            title = await _request_title(
                first_text,
                max_input_chars=max_input_chars,
                max_tokens=max_tokens,
                max_title_length=max_title_length,
                thinking_level=thinking_level,
                timeout_seconds=timeout_seconds,
                api=api,
            )
            if title:
                api.set_session_name(title)
                api.ui.notify(f"Session title: {title}", level="info")
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("session title generation failed: %s", exc, exc_info=True)
        finally:
            state["generating"] = False

    @api.on(INPUT)
    async def on_input(event: Any, payload: Any, ctx: Any) -> None:
        if state["title_generated"] or state["first_user_text"] is not None:
            return

        text = getattr(payload, "text", "") or ""
        text = text.strip()
        if not text or _is_command(text):
            return

        state["first_user_text"] = text[:max_input_chars]

    @api.on(AGENT_SETTLED)
    async def on_agent_settled(event: Any, payload: Any, ctx: Any) -> None:
        if state["title_generated"] or state["first_user_text"] is None:
            return

        first_text = state["first_user_text"]
        state["title_generated"] = True

        # Fire-and-forget so we don't block the settled event.
        future = asyncio.ensure_future(_generate_title(first_text))
        state["_futures"].add(future)
        future.add_done_callback(state["_futures"].discard)


# ---------------------------------------------------------------------------
# Title generation via the current model
# ---------------------------------------------------------------------------


async def _request_title(
    first_text: str,
    *,
    max_input_chars: int,
    max_tokens: int,
    max_title_length: int,
    thinking_level: str,
    timeout_seconds: int,
    api: Any,
) -> str | None:
    from vtx.ai.dynamic_models import find_dynamic_model
    from vtx.ai.models import ApiType, get_model

    model = getattr(api, "model", None) or ""
    if not model:
        return None

    # Resolve model info so we know the provider / api type.
    model_info = get_model(model, None)
    if model_info is None:
        model_info = find_dynamic_model(model, None)
    if model_info is None:
        return None

    provider_name = getattr(model_info, "provider", None) or ""
    api_type = getattr(model_info, "api", None)
    if not isinstance(api_type, ApiType):
        return None

    # Resolve API key / headers the same way the runtime does.
    api_key = resolve_api_key(None, env_vars=[], base_url=None, auth_mode="auto")
    if not api_key:
        api_key = get_env_api_key(provider_name)

    provider_config = ProviderConfig(
        api_key=api_key,
        base_url=None,
        model=model,
        max_tokens=max_tokens,
        thinking_level=thinking_level,
        provider=provider_name,
        session_id=None,
    )

    try:
        provider = create_provider(api_type, provider_config)
    except Exception:
        return None

    truncated = first_text[:max_input_chars]
    user_message: dict[str, Any] = {
        "role": "user",
        "content": [{"type": "text", "text": truncated}],
    }

    try:
        response = await asyncio.wait_for(
            provider.chat_with_retry(
                messages=[user_message], system_prompt=_TITLE_SYSTEM_PROMPT, max_tokens=max_tokens
            ),
            timeout=timeout_seconds,
        )
    except Exception:
        return None

    raw = ""
    if hasattr(response, "content") and response.content:
        raw = str(response.content)
    elif isinstance(response, dict):
        choices = response.get("choices") or []
        if choices:
            msg = choices[0].get("message") or {}
            raw = msg.get("content") or ""

    return _clean_title(raw, max_length=max_title_length)
