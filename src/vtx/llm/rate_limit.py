"""Internal rate-limit manager for LLM providers.

Intercepts rate-limit errors (429 / Retry-After) at the provider stream
layer and retries with exponential backoff + jitter.  Transparent to the
caller — a successful stream is returned as if no rate limit occurred.

Usage (automatic via BaseProvider.stream):
    The manager is instantiated lazily on first use and shared across all
    providers.  Each call to ``retry_stream`` tracks attempt count per
    provider so backoff resets between independent requests.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseProvider, LLMStream

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_DELAY = 60.0  # seconds
JITTER_RANGE = 0.5  # ±50 % of computed delay


# ---------------------------------------------------------------------------
# Rate-limit detection helpers
# ---------------------------------------------------------------------------


def is_rate_limit_error(error: Exception) -> bool:
    """Return True if *error* represents a rate-limit / 429 response."""
    msg = str(error).lower()
    name = type(error).__name__

    # Direct keyword match
    if "rate limit" in msg or "too many requests" in msg or "429" in msg:
        return True

    # Transient upstream errors from gateways (Kilo, OpenRouter, etc.)
    if any(s in msg for s in ("provider returned error", "overloaded", "capacity")):
        return True

    # httpx / openai / anthropic SDK error classes
    if name in ("RateLimitError", "TooManyRequestsError"):
        return True

    # HTTPStatusError variants — check status code attribute
    status = getattr(error, "status_code", None)
    return status == 429


def parse_retry_after(error: Exception) -> float | None:
    """Try to extract a ``Retry-After`` value (seconds) from the error."""
    # Some SDKs expose headers on the error object
    headers = getattr(error, "headers", None)
    if headers is None:
        resp = getattr(error, "response", None)
        headers = getattr(resp, "headers", None) if resp else None

    if headers:
        raw = None
        get_fn = getattr(headers, "get", None)
        if get_fn is not None:
            raw = get_fn("Retry-After") or get_fn("retry-after")
        elif isinstance(headers, dict):
            raw = headers.get("Retry-After") or headers.get("retry-after")

        if raw is not None:
            try:
                return float(raw)
            except (ValueError, TypeError):
                pass

    return None


# ---------------------------------------------------------------------------
# RateLimitManager
# ---------------------------------------------------------------------------


@dataclass
class _ProviderState:
    attempts: int = 0
    last_error_time: float = 0.0


class RateLimitManager:
    """Stateful manager that applies exponential backoff on rate-limit errors.

    Instantiate once and share across providers.  Call ``should_retry`` to
    check whether a retry is allowed, then ``wait`` to block for the
    appropriate delay.  Call ``reset`` after a successful stream to clear
    the attempt counter.
    """

    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self._states: dict[str, _ProviderState] = {}

    def _state(self, provider_name: str) -> _ProviderState:
        if provider_name not in self._states:
            self._states[provider_name] = _ProviderState()
        return self._states[provider_name]

    def should_retry(self, provider_name: str, error: Exception) -> bool:
        """Return True if the error is retryable and retries remain."""
        if not is_rate_limit_error(error):
            return False
        state = self._state(provider_name)
        return state.attempts < self.max_retries

    def wait_delay(self, provider_name: str, error: Exception) -> float:
        """Compute and return the delay (seconds) before the next retry.

        Respects ``Retry-After`` headers when present, otherwise uses
        exponential backoff with jitter.
        """
        state = self._state(provider_name)
        state.attempts += 1
        state.last_error_time = time.monotonic()

        # Prefer server-provided Retry-After
        retry_after = parse_retry_after(error)
        if retry_after is not None:
            delay = min(retry_after, self.max_delay)
        else:
            delay = min(self.base_delay * (2 ** (state.attempts - 1)), self.max_delay)

        # Add jitter
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)
        delay = max(0.1, delay + jitter)

        logger.info(
            "Rate limit hit for %s (attempt %d/%d), retrying in %.1fs",
            provider_name,
            state.attempts,
            self.max_retries,
            delay,
        )
        return delay

    def reset(self, provider_name: str) -> None:
        """Reset attempt counter after a successful request."""
        state = self._state(provider_name)
        state.attempts = 0

    async def retry_stream(
        self,
        provider: BaseProvider,
        messages: list,
        *,
        system_prompt: str | None = None,
        tools=None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMStream:
        """Call ``provider._stream_impl`` with internal rate-limit retries.

        On success the attempt counter is reset.  On exhausted retries the
        last error is re-raised.
        """
        provider_name = getattr(provider, "name", "unknown")
        last_error: Exception | None = None

        for _attempt in range(self.max_retries + 1):
            try:
                stream = await provider._stream_impl(
                    messages,
                    system_prompt=system_prompt,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self.reset(provider_name)
                return stream
            except Exception as exc:
                last_error = exc
                if not self.should_retry(provider_name, exc):
                    raise
                delay = self.wait_delay(provider_name, exc)
                await asyncio.sleep(delay)

        # Should not reach here, but safety net
        assert last_error is not None
        raise last_error


# Module-level singleton
rate_limit_manager = RateLimitManager()
