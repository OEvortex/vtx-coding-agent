import pytest

from vtx.llm.rate_limit import RateLimitManager, is_rate_limit_error, parse_retry_after


class TestIsRateLimitError:
    def test_rate_limit_keyword(self):
        assert is_rate_limit_error(RuntimeError("Rate limit exceeded"))

    def test_too_many_requests(self):
        assert is_rate_limit_error(RuntimeError("Too many requests"))

    def test_429_in_message(self):
        assert is_rate_limit_error(RuntimeError("HTTP 429"))

    def test_rate_limit_error_class_name(self):
        exc = type("RateLimitError", (Exception,), {})()
        assert is_rate_limit_error(exc)

    def test_status_code_429(self):
        exc = Exception("err")
        exc.status_code = 429  # type: ignore[attr-defined]
        assert is_rate_limit_error(exc)

    def test_non_rate_limit_error(self):
        assert not is_rate_limit_error(RuntimeError("Connection reset"))

    def test_provider_returned_error(self):
        assert is_rate_limit_error(RuntimeError("Provider returned error"))

    def test_overloaded_error(self):
        assert is_rate_limit_error(RuntimeError("Model is overloaded"))

    def test_capacity_error(self):
        assert is_rate_limit_error(RuntimeError("At capacity, try again later"))

    def test_status_code_500(self):
        exc = Exception("err")
        exc.status_code = 500  # type: ignore[attr-defined]
        assert not is_rate_limit_error(exc)


class TestParseRetryAfter:
    def test_from_headers_dict(self):
        exc = Exception("err")
        exc.headers = {"Retry-After": "5"}
        assert parse_retry_after(exc) == 5.0

    def test_from_response_headers(self):
        resp = type("Resp", (), {"headers": {"retry-after": "10"}})()
        exc = Exception("err")
        exc.response = resp
        assert parse_retry_after(exc) == 10.0

    def test_no_headers(self):
        assert parse_retry_after(Exception("err")) is None

    def test_non_numeric_retry_after(self):
        exc = Exception("err")
        exc.headers = {"Retry-After": "bad"}
        assert parse_retry_after(exc) is None


class TestRateLimitManager:
    def test_should_retry_on_rate_limit(self):
        mgr = RateLimitManager(max_retries=3)
        err = RuntimeError("Rate limit exceeded")
        assert mgr.should_retry("test", err)

    def test_should_not_retry_on_non_rate_limit(self):
        mgr = RateLimitManager(max_retries=3)
        err = RuntimeError("Connection reset")
        assert not mgr.should_retry("test", err)

    def test_should_not_retry_after_exhaustion(self):
        mgr = RateLimitManager(max_retries=2)
        err = RuntimeError("Rate limit exceeded")
        mgr.wait_delay("test", err)
        mgr.wait_delay("test", err)
        assert not mgr.should_retry("test", err)

    def test_reset_clears_attempts(self):
        mgr = RateLimitManager(max_retries=3)
        err = RuntimeError("Rate limit exceeded")
        mgr.wait_delay("test", err)
        mgr.wait_delay("test", err)
        mgr.reset("test")
        assert mgr.should_retry("test", err)

    def test_delay_exponential_backoff(self):
        mgr = RateLimitManager(max_retries=5, base_delay=1.0, max_delay=60.0)
        err = RuntimeError("Rate limit exceeded")
        d1 = mgr.wait_delay("test", err)
        d2 = mgr.wait_delay("test", err)
        d3 = mgr.wait_delay("test", err)
        # With jitter, just check rough ordering
        assert d1 < d2 + 1.0
        assert d2 < d3 + 1.0

    def test_delay_respects_retry_after(self):
        mgr = RateLimitManager(max_retries=3)
        err = RuntimeError("Rate limit exceeded")
        err.headers = {"Retry-After": "2"}
        delay = mgr.wait_delay("test", err)
        assert delay >= 1.0  # 2s ± jitter, minimum 0.1
        assert delay <= 3.0

    def test_delay_capped_at_max(self):
        mgr = RateLimitManager(max_retries=10, base_delay=1.0, max_delay=5.0)
        err = RuntimeError("Rate limit exceeded")
        for _ in range(8):
            mgr.wait_delay("test", err)
        delay = mgr.wait_delay("test", err)
        assert delay <= 5.0 * 1.5 + 0.1  # max_delay + max jitter + margin


class TestRateLimitManagerRetryStream:
    @pytest.mark.asyncio
    async def test_successful_first_attempt(self):
        from unittest.mock import AsyncMock, MagicMock

        mgr = RateLimitManager(max_retries=3)
        provider = MagicMock()
        provider.name = "test"
        expected_stream = MagicMock()
        provider._stream_impl = AsyncMock(return_value=expected_stream)

        result = await mgr.retry_stream(provider, [])
        assert result is expected_stream
        assert provider._stream_impl.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit_then_succeeds(self):
        from unittest.mock import AsyncMock, MagicMock

        mgr = RateLimitManager(max_retries=3, base_delay=0.01)
        provider = MagicMock()
        provider.name = "test"
        expected_stream = MagicMock()
        provider._stream_impl = AsyncMock(
            side_effect=[RuntimeError("Rate limit exceeded"), expected_stream]
        )

        result = await mgr.retry_stream(provider, [])
        assert result is expected_stream
        assert provider._stream_impl.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_exhausting_retries(self):
        from unittest.mock import AsyncMock, MagicMock

        mgr = RateLimitManager(max_retries=2, base_delay=0.01)
        provider = MagicMock()
        provider.name = "test"
        provider._stream_impl = AsyncMock(side_effect=RuntimeError("Rate limit exceeded"))

        with pytest.raises(RuntimeError, match="Rate limit exceeded"):
            await mgr.retry_stream(provider, [])
        assert provider._stream_impl.call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_rate_limit_error_raises_immediately(self):
        from unittest.mock import AsyncMock, MagicMock

        mgr = RateLimitManager(max_retries=3, base_delay=0.01)
        provider = MagicMock()
        provider.name = "test"
        provider._stream_impl = AsyncMock(side_effect=RuntimeError("Connection reset"))

        with pytest.raises(RuntimeError, match="Connection reset"):
            await mgr.retry_stream(provider, [])
        assert provider._stream_impl.call_count == 1
