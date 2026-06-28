"""Tests for the dynamic OpenAI-compatible provider model registry."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from vtx.llm import (
    DYNAMIC_PROVIDERS,
    DynamicProviderConfig,
    get_dynamic_models,
    get_provider_models,
    refresh_all_providers,
    refresh_provider,
    register_dynamic_provider,
)
from vtx.llm.dynamic_models import (
    CachedCatalog,
    DynamicModelEntry,
    _cache_path,
    _is_free_model,
    _parse_models,
    _read_cache,
    _write_cache,
    find_dynamic_model,
    get_cache_dir,
    get_dynamic_provider_headers,
)
from vtx.llm.models import Model


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    # Clear all in-memory dynamic models from previous tests so cache lookups
    # start clean. We do this by re-pointing at an empty tmp dir.
    for path in tmp_path.glob("*.json"):
        path.unlink()


# =================================================================================================
# Registry basics
# =================================================================================================


def test_builtin_providers_registered():
    assert {"airouter", "opencode", "kilo", "tokenrouter"} <= DYNAMIC_PROVIDERS.keys()


def test_each_builtin_provider_has_base_url_and_env_var():
    for name, cfg in DYNAMIC_PROVIDERS.items():
        assert cfg.base_url.startswith("http"), f"{name} has invalid base_url"
        # env_var may be empty for keyless providers (e.g. ollama) — those
        # are identified by api_key_optional=True.


def test_kilo_has_editor_header():
    kilo = DYNAMIC_PROVIDERS["kilo"]
    assert "X-KILOCODE-EDITORNAME" in kilo.headers


def test_register_dynamic_provider_replaces():
    custom = DynamicProviderConfig(
        name="custom-test", base_url="https://example.com/v1", env_var="CUSTOM_TEST_API_KEY"
    )
    register_dynamic_provider(custom)
    assert DYNAMIC_PROVIDERS["custom-test"] is custom
    # Cleanup
    DYNAMIC_PROVIDERS.pop("custom-test", None)


# =================================================================================================
# Free model detection
# =================================================================================================


@pytest.mark.parametrize(
    ("name", "prompt", "completion", "pricing_known", "expected"),
    [
        # Pricing-known + zero cost → free
        ("llama-3.1-8b", 0.0, 0.0, True, True),
        # Pricing-known + non-zero cost → paid
        ("gpt-5", 1.0, 2.0, True, False),
        # Pricing-known + zero cost but contains "free" in name → still free
        ("free-llama", 0.0, 0.0, True, True),
        # Pricing-known + paid but contains "free" in name → free
        ("free-experimental", 1.0, 2.0, True, True),
        # Pricing unknown + name has "free" → free
        ("opencode-free", 0.0, 0.0, False, True),
        # Pricing unknown + name without "free" → not free
        ("gpt-5", 0.0, 0.0, False, False),
        # Pricing unknown + "friend" is not "free" → not free
        ("opencode-friend", 0.0, 0.0, False, False),
    ],
)
def test_is_free_model(
    name: str, prompt: float, completion: float, pricing_known: bool, expected: bool
):
    assert _is_free_model(name, prompt, completion, pricing_known) is expected


# =================================================================================================
# Parsing
# =================================================================================================


def test_parse_models_skips_image_output_models():
    raw = [
        {"id": "llm", "name": "LLM"},
        {"id": "img", "name": "IMG", "output_modalities": ["image"]},
    ]
    parsed = _parse_models(raw)
    assert [m.id for m in parsed] == ["llm"]


def test_parse_models_extracts_capabilities():
    raw = [
        {
            "id": "vision-llm",
            "name": "Vision LLM",
            "architecture": {"input_modalities": ["text", "image"]},
            "context_length": 32768,
            "max_completion_tokens": 4096,
            "pricing": {"prompt": "0", "completion": "0"},
            "supports_reasoning": True,
        }
    ]
    parsed = _parse_models(raw)
    assert len(parsed) == 1
    entry = parsed[0]
    assert entry.supports_images is True
    assert entry.supports_thinking is True
    assert entry.context_window == 32768
    assert entry.max_tokens == 4096
    assert entry.is_free is True
    assert entry.pricing_known is True


def test_parse_models_drops_corrupt_pricing():
    raw = [{"id": "m", "name": "M", "pricing": {"prompt": "not-a-number"}}]
    parsed = _parse_models(raw)
    assert parsed[0].pricing_known is False
    assert parsed[0].is_free is False


def test_parse_models_does_not_hardcode_minimax_or_deepseek_r1() -> None:
    """Regression guard: we removed the per-model name hacks
    (minimax-m3 / minimax-text-01 / deepseek-r1 / qwq). Capability detection
    must now come from the catalog payload or models.dev, not from a hardcoded
    name list. If a gateway returns a minimal OpenAI-standard catalog entry
    for these models, the parser must report ``supports_thinking=False``
    rather than guessing.
    """
    raw = [
        {"id": "MiniMax-M3", "object": "model", "owned_by": "custom"},
        {"id": "deepseek-r1-distill", "object": "model", "owned_by": "custom"},
        {"id": "qwq-32b-preview", "object": "model", "owned_by": "custom"},
    ]
    parsed = _parse_models(raw, models_dev={})
    assert {m.id for m in parsed} == {"MiniMax-M3", "deepseek-r1-distill", "qwq-32b-preview"}
    for entry in parsed:
        assert entry.supports_thinking is False
        assert entry.context_window is None


def test_parse_models_uses_models_dev_for_minimax_m3() -> None:
    """Positive case: when models.dev has the spec, the parser must use it
    (not the removed name-based fallback) to surface real capabilities.
    """
    raw = [{"id": "MiniMax-M3", "object": "model", "owned_by": "custom"}]
    models_dev = {
        "minimax/MiniMax-M3": {
            "id": "minimax/MiniMax-M3",
            "reasoning": True,
            "limit": {"context": 512000, "output": 64000},
            "modalities": {"input": ["text", "image"], "output": ["text"]},
        }
    }
    parsed = _parse_models(raw, models_dev=models_dev)
    entry = parsed[0]
    assert entry.supports_thinking is True
    assert entry.supports_images is True
    assert entry.context_window == 512000
    assert entry.max_tokens == 64000


# =================================================================================================
# Cache IO
# =================================================================================================


def test_cache_roundtrip(tmp_path: Path):
    catalog = CachedCatalog(
        provider="kilo",
        fetched_at=1234.0,
        models=[DynamicModelEntry(id="m1", name="M1", is_free=True)],
    )
    _write_cache(catalog)
    loaded = _read_cache("kilo")
    assert loaded is not None
    assert loaded.provider == "kilo"
    assert loaded.fetched_at == 1234.0
    assert loaded.models[0].id == "m1"
    assert loaded.models[0].is_free is True


def test_cache_path_uses_safe_filename():
    assert _cache_path("kilo").name == "kilo.json"
    # Slashes and ".." are stripped so the path can never escape the cache dir.
    path = _cache_path("../evil")
    assert "/" not in path.name
    assert ".." not in path.name
    assert path.name.endswith("evil.json")


def test_read_cache_returns_none_for_missing(tmp_path: Path):
    assert _read_cache("does-not-exist") is None


def test_read_cache_discards_corrupt_json(tmp_path: Path, caplog):
    bad = tmp_path / "kilo.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert _read_cache("kilo") is None


def test_get_cache_dir_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path / "models"))
    assert get_cache_dir() == tmp_path / "models"


# =================================================================================================
# Async fetch + cache TTL
# =================================================================================================


def _make_response(payload: object, status: int = 200) -> httpx.Response:
    request = httpx.Request("GET", "https://api.kilo.ai/api/gateway/models")
    return httpx.Response(status, json=payload, request=request)


def _stub_http_client(monkeypatch: pytest.MonkeyPatch, payload: object, status: int = 200):
    """Patch httpx.AsyncClient.get used inside dynamic_models."""
    mock_response = _make_response(payload, status)
    captured: dict[str, list[dict]] = {}

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None):
            captured.setdefault("calls", []).append({"url": url, "headers": dict(headers or {})})
            return mock_response

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)
    return captured


@pytest.mark.asyncio
async def test_async_fetch_writes_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("KILO_API_KEY", "test-key")
    payload = {
        "data": [
            {
                "id": "kilo/llama-3",
                "name": "Llama 3",
                "pricing": {"prompt": "0", "completion": "0"},
                "context_length": 8192,
            }
        ]
    }
    _stub_http_client(monkeypatch, payload)

    from vtx.llm.dynamic_models import _async_fetch_catalog

    catalog = await _async_fetch_catalog(DYNAMIC_PROVIDERS["kilo"], api_key="test-key", force=True)
    assert len(catalog.models) == 1
    assert catalog.models[0].id == "kilo/llama-3"
    # Cache should now exist on disk
    assert _read_cache("kilo") is not None


@pytest.mark.asyncio
async def test_async_fetch_falls_back_to_stale_cache_on_5xx(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    # Seed a stale cache
    _write_cache(
        CachedCatalog(
            provider="kilo", fetched_at=0.0, models=[DynamicModelEntry(id="stale", name="Stale")]
        )
    )

    request = httpx.Request("GET", "https://api.kilo.ai/api/gateway/models")

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None):
            return httpx.Response(503, request=request)

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)

    from vtx.llm.dynamic_models import _async_fetch_catalog

    catalog = await _async_fetch_catalog(DYNAMIC_PROVIDERS["kilo"], api_key="k", force=True)
    assert catalog.models[0].id == "stale"


@pytest.mark.asyncio
async def test_async_fetch_auth_error_uses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    _write_cache(
        CachedCatalog(
            provider="kilo", fetched_at=0.0, models=[DynamicModelEntry(id="cached", name="Cached")]
        )
    )

    request = httpx.Request("GET", "https://api.kilo.ai/api/gateway/models")

    class _StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers=None):
            return httpx.Response(401, request=request)

    monkeypatch.setattr(httpx, "AsyncClient", _StubClient)

    from vtx.llm.dynamic_models import _async_fetch_catalog

    catalog = await _async_fetch_catalog(DYNAMIC_PROVIDERS["kilo"], api_key="k", force=True)
    assert catalog.models[0].id == "cached"


def test_ttl_skips_network_when_cache_fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    _write_cache(
        CachedCatalog(
            provider="kilo",
            fetched_at=99999999999.0,  # far in the future
            models=[DynamicModelEntry(id="fresh", name="Fresh")],
        )
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("HTTP should not be called when cache is fresh")

    monkeypatch.setattr(httpx, "AsyncClient", _fail_if_called)
    models = get_provider_models("kilo")
    assert models and models[0].id == "fresh"


def test_get_provider_models_returns_empty_for_unknown():
    assert get_provider_models("does-not-exist") == []


# =================================================================================================
# Sync refresh entry points
# =================================================================================================


def test_refresh_provider_writes_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("KILO_API_KEY", "test-key")
    payload = {"data": [{"id": "m", "name": "M"}]}
    _stub_http_client(monkeypatch, payload)
    count = refresh_provider("kilo")
    assert count == 1
    assert _read_cache("kilo") is not None


def test_refresh_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown dynamic provider"):
        refresh_provider("nope")


def test_refresh_all_providers_skips_failures(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("KILO_API_KEY", "test-key")
    payload = {"data": [{"id": "m", "name": "M"}]}
    _stub_http_client(monkeypatch, payload)
    counts = refresh_all_providers()
    assert "kilo" in counts
    assert counts["kilo"] == 1


# =================================================================================================
# find_dynamic_model + headers
# =================================================================================================


def test_find_dynamic_model_uses_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    _write_cache(
        CachedCatalog(
            provider="kilo",
            fetched_at=99999999999.0,
            models=[DynamicModelEntry(id="gpt-5", name="GPT-5", is_free=True)],
        )
    )
    found = find_dynamic_model("gpt-5", "kilo")
    assert found is not None
    assert isinstance(found, Model)
    assert found.provider == "kilo"
    assert found.base_url == DYNAMIC_PROVIDERS["kilo"].base_url


def test_find_dynamic_model_returns_none_when_missing(tmp_path: Path):
    assert find_dynamic_model("nope", "kilo") is None
    assert find_dynamic_model("nope", "unknown-provider") is None


def test_get_dynamic_provider_headers():
    headers = get_dynamic_provider_headers("kilo")
    assert headers["X-KILOCODE-EDITORNAME"] == "vtx"
    assert get_dynamic_provider_headers("airouter") == {}
    assert get_dynamic_provider_headers("unknown") == {}


# =================================================================================================
# get_dynamic_models
# =================================================================================================


def test_get_dynamic_models_returns_static_shape(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("vtx.llm.dynamic_models._read_models_dev_sync", lambda: {})
    _write_cache(
        CachedCatalog(
            provider="kilo",
            fetched_at=99999999999.0,
            models=[
                DynamicModelEntry(
                    id="gpt-5",
                    name="GPT-5",
                    context_window=100_000,
                    max_tokens=8192,
                    supports_images=True,
                    supports_thinking=True,
                )
            ],
        )
    )
    models = get_dynamic_models()
    kilo_models = [m for m in models if m.provider == "kilo"]
    assert len(kilo_models) == 1
    assert kilo_models[0].id == "gpt-5"
    assert kilo_models[0].context_window == 100_000
    assert kilo_models[0].supports_images is True


def test_get_all_models_dedupes_dynamic_overlap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """``get_all_models`` must not return a dynamic model twice.

    ``get_all_catalog_models`` already includes cached dynamic entries via
    ``get_fetched_models``, and ``get_dynamic_models`` returns the same
    cache. The merged result used to contain every dynamic model twice,
    which made the /model picker show each model with a duplicate row.
    """
    from vtx.llm import get_all_models

    monkeypatch.setenv("VTX_MODELS_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr("vtx.llm.dynamic_models._read_models_dev_sync", lambda: {})
    _write_cache(
        CachedCatalog(
            provider="kilo",
            fetched_at=99999999999.0,
            models=[
                DynamicModelEntry(
                    id="gpt-5",
                    name="GPT-5",
                    context_window=100_000,
                    max_tokens=8192,
                    supports_images=True,
                    supports_thinking=True,
                ),
                DynamicModelEntry(
                    id="claude-fable-5",
                    name="Claude",
                    context_window=200_000,
                    max_tokens=8192,
                    supports_images=True,
                    supports_thinking=False,
                ),
            ],
        )
    )

    models = get_all_models()
    keys = [(m.provider, m.id) for m in models]
    assert len(keys) == len(set(keys)), f"duplicate models in picker: {keys}"
    kilo = [m for m in models if m.provider == "kilo"]
    assert sorted(m.id for m in kilo) == ["claude-fable-5", "gpt-5"]


def test_dedupe_models_preserves_first_occurrence():
    from vtx.llm.models import ApiType, dedupe_models

    a = Model(
        id="a",
        provider="p",
        api=ApiType(ApiType.OPENAI_SDK),
        base_url="",
        max_tokens=1,
        supports_images=False,
        supports_thinking=False,
    )
    b = Model(
        id="b",
        provider="p",
        api=ApiType(ApiType.OPENAI_SDK),
        base_url="",
        max_tokens=2,
        supports_images=False,
        supports_thinking=False,
    )
    a_dup = Model(
        id="a",
        provider="p",
        api=ApiType(ApiType.OPENAI_SDK),
        base_url="",
        max_tokens=999,
        supports_images=False,
        supports_thinking=False,
    )
    deduped = dedupe_models([a, b, a_dup])
    assert deduped == [a, b]
