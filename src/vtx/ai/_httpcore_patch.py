"""Patch httpcore/httpcore2 async generators that mishandle GeneratorExit.

httpcore 1.0.9 (vendored as httpcore2 in some uv tool envs) has a bug in
``HTTP11ConnectionByteStream.__aiter__``:

    except BaseException as exc:
        await self.aclose()
        raise exc

When the outer stream is closed early (user ESC, tool-call stall timeout,
or normal turn cancellation), Python throws ``GeneratorExit`` into the
async generator at its ``yield``. The blanket ``except BaseException``
catches it and **re-raises** ``GeneratorExit``. An async generator
must *return* (or raise ``StopAsyncIteration``) after ``aclose()``'s
``athrow(GeneratorExit)``, not re-raise ``GeneratorExit`` - otherwise
CPython raises ``RuntimeError: generator didn't stop after athrow()``,
which surfaces as::

    an error occurred during closing of asynchronous generator
    <async_generator object PoolByteStream.__aiter__ at ...>
    RuntimeError: generator didn't stop after athrow()

The fix: handle ``GeneratorExit`` explicitly (return) and suppress the
follow-on ``RuntimeError`` in ``safe_async_iterate``'s cleanup where it
exists.

This module is imported for its side effect at startup (see
``vtx.ai.base``). It patches *both* ``httpcore`` and ``httpcore2`` if
present, idempotently.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import logging
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)

_PATCHED = False


def _patch_safe_async_iterate(utils_mod: Any) -> None:
    """Make ``safe_async_iterate`` suppress the buggy RuntimeError."""
    orig = getattr(utils_mod, "safe_async_iterate", None)
    if orig is None or getattr(orig, "_vtx_patched", False):
        return

    from contextlib import asynccontextmanager
    from inspect import isasyncgen

    @asynccontextmanager
    async def patched_safe_async_iterate(iterable_or_iterator):  # type: ignore[no-untyped-def]
        iterator = (
            iterable_or_iterator
            if hasattr(iterable_or_iterator, "__anext__")
            else iterable_or_iterator.__aiter__()  # type: ignore[union-attr]
        )
        try:
            yield iterator
        finally:
            if isasyncgen(iterator):
                try:
                    await iterator.aclose()
                except RuntimeError as exc:
                    if "generator didn't stop after athrow" in str(exc):
                        pass
                    else:
                        raise
                except BaseException:
                    pass

    patched_safe_async_iterate._vtx_patched = True  # ty: ignore[unresolved-attribute]
    utils_mod.safe_async_iterate = patched_safe_async_iterate


def _patch_http11(http11_mod: Any, pkg: str) -> None:
    """Fix ``HTTP11ConnectionByteStream.__aiter__`` to handle GeneratorExit."""
    cls = getattr(http11_mod, "HTTP11ConnectionByteStream", None)
    if cls is None or getattr(cls.__aiter__, "_vtx_patched", False):
        return

    orig_aiter = cls.__aiter__
    try:
        orig_src = inspect.getsource(orig_aiter)
    except (OSError, TypeError):
        orig_src = ""
    uses_safe = "safe_async_iterate" in orig_src

    if uses_safe:
        # httpcore2 variant that uses safe_async_iterate
        async def patched_aiter(self) -> AsyncGenerator[bytes, None]:  # type: ignore[no-untyped-def]
            # Resolve at call time so we pick up the patched safe_async_iterate.
            try:
                utils_mod = importlib.import_module(f"{pkg}._utils")
                safe_async_iterate = getattr(utils_mod, "safe_async_iterate", None)
            except ImportError:
                safe_async_iterate = None
            # Fallback import for AsyncShieldCancellation
            try:
                asc_mod = importlib.import_module(f"{pkg}._synchronization")
                _asc = asc_mod.AsyncShieldCancellation
            except ImportError:
                from contextlib import nullcontext as _asc

            _Trace = getattr(http11_mod, "Trace", None)
            _logger = getattr(http11_mod, "logger", logger)
            kwargs: dict[str, Any] = {"request": self._request}
            try:
                if _Trace is not None and safe_async_iterate is not None:
                    async with _Trace("receive_response_body", _logger, self._request, kwargs):
                        async with safe_async_iterate(
                            self._connection._receive_response_body(**kwargs)  # type: ignore[union-attr]
                        ) as iterator:
                            async for chunk in iterator:
                                yield chunk
                elif safe_async_iterate is not None:
                    async with safe_async_iterate(
                        self._connection._receive_response_body(**kwargs)  # type: ignore[union-attr]
                    ) as iterator:
                        async for chunk in iterator:
                            yield chunk
                elif _Trace is not None:
                    async with _Trace("receive_response_body", _logger, self._request, kwargs):
                        async for chunk in self._connection._receive_response_body(**kwargs):  # type: ignore[union-attr]
                            yield chunk
                else:
                    async for chunk in self._connection._receive_response_body(**kwargs):  # type: ignore[union-attr]
                        yield chunk
            except GeneratorExit:
                with _asc(), contextlib.suppress(BaseException):
                    await self.aclose()
                return
            except BaseException as exc:
                with _asc(), contextlib.suppress(BaseException):
                    await self.aclose()
                raise exc

    else:
        # httpcore 1.0.9 variant – direct async for
        async def patched_aiter(self) -> AsyncGenerator[bytes, None]:  # type: ignore[no-untyped-def]
            try:
                asc_mod = importlib.import_module(f"{pkg}._synchronization")
                _asc = asc_mod.AsyncShieldCancellation
            except ImportError:
                from contextlib import nullcontext as _asc

            _Trace = getattr(http11_mod, "Trace", None)
            _logger = getattr(http11_mod, "logger", logger)
            kwargs: dict[str, Any] = {"request": self._request}
            try:
                if _Trace is not None:
                    async with _Trace("receive_response_body", _logger, self._request, kwargs):
                        async for chunk in self._connection._receive_response_body(**kwargs):  # type: ignore[union-attr]
                            yield chunk
                else:
                    async for chunk in self._connection._receive_response_body(**kwargs):  # type: ignore[union-attr]
                        yield chunk
            except GeneratorExit:
                with _asc(), contextlib.suppress(BaseException):
                    await self.aclose()
                return
            except BaseException as exc:
                with _asc(), contextlib.suppress(BaseException):
                    await self.aclose()
                raise exc

    patched_aiter._vtx_patched = True  # ty: ignore[invalid-assignment]
    cls.__aiter__ = patched_aiter  # type: ignore[method-assign]
    cls._vtx_orig_aiter = orig_aiter


def _patch_pool(pool_mod: Any, pkg: str) -> None:
    """Make PoolByteStream robust."""
    cls = getattr(pool_mod, "PoolByteStream", None)
    if cls is None or getattr(cls.__aiter__, "_vtx_patched", False):
        return
    orig = cls.__aiter__
    try:
        orig_src = inspect.getsource(orig)
    except (OSError, TypeError):
        orig_src = ""
    uses_safe = "safe_async_iterate" in orig_src

    if uses_safe:

        async def patched_pool_aiter(self) -> AsyncGenerator[bytes, None]:  # type: ignore[no-untyped-def]
            try:
                utils_mod = importlib.import_module(f"{pkg}._utils")
                safe_async_iterate = getattr(utils_mod, "safe_async_iterate", None)
            except ImportError:
                safe_async_iterate = None
            if safe_async_iterate is None:
                async for chunk in orig(self):  # type: ignore[misc]
                    yield chunk
                return
            try:
                async with safe_async_iterate(self._stream) as iterator:  # type: ignore[attr-defined]
                    async for chunk in iterator:
                        yield chunk
            except GeneratorExit:
                return
            except BaseException:
                raise

    else:

        async def patched_pool_aiter(self) -> AsyncGenerator[bytes, None]:  # type: ignore[no-untyped-def]
            try:
                asc_mod = importlib.import_module(f"{pkg}._synchronization")
                _asc = asc_mod.AsyncShieldCancellation
            except ImportError:
                from contextlib import nullcontext as _asc

            try:
                async for part in self._stream:  # type: ignore[attr-defined]
                    yield part
            except GeneratorExit:
                with _asc(), contextlib.suppress(BaseException):
                    await self.aclose()
                return
            except BaseException as exc:
                with _asc(), contextlib.suppress(BaseException):
                    await self.aclose()
                raise exc

    patched_pool_aiter._vtx_patched = True  # ty: ignore[invalid-assignment]
    cls.__aiter__ = patched_pool_aiter  # type: ignore[method-assign]
    cls._vtx_orig_aiter = orig


def apply_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True
    for pkg in ("httpcore", "httpcore2"):
        # safe_async_iterate only exists in httpcore2
        try:
            utils_mod = importlib.import_module(f"{pkg}._utils")
            _patch_safe_async_iterate(utils_mod)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("httpcore patch: safe_async_iterate %s failed: %s", pkg, exc)

        try:
            http11_mod = importlib.import_module(f"{pkg}._async.http11")
            _patch_http11(http11_mod, pkg)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("httpcore patch: http11 %s failed: %s", pkg, exc)

        try:
            pool_mod = importlib.import_module(f"{pkg}._async.connection_pool")
            _patch_pool(pool_mod, pkg)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("httpcore patch: pool %s failed: %s", pkg, exc)
