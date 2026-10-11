"""Custom-provider files must be re-read on ``/reload``, deletions included.

The catalog caches its provider-file scan by design -- the model picker reads it
on every keystroke, so re-globbing per read would be per-keystroke I/O. The cost
of that choice is that an edit under the providers directory is invisible until
something asks for a reload, and a *deleted* file's provider stays in the
catalog forever. These pin the reload path that makes an edit land and a
deletion take effect.
"""

from __future__ import annotations

import pytest

from vtx.ai import provider_catalog
from vtx.ai.provider_catalog import custom_provider_slugs, reload_custom_providers

PROVIDER_BODY = "\n".join(
    [
        "slug: {slug}",
        "display_name: E2E Gateway",
        "family: openai_compat",
        "base_url: {base_url}",
        "api_key_env: E2E_GATEWAY_KEY",
        "known_models: [e2e-model]",
    ]
)


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    """A sandboxed providers directory, with the catalog resynced to it."""
    xdg = tmp_path / "xdg"
    providers = xdg / "vtx" / "providers"
    providers.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    # A previous test may have populated the cache from the real directories.
    reload_custom_providers()
    yield providers
    reload_custom_providers()


def _write(providers_dir, *, slug: str = "e2e-gateway", base_url: str = "https://gw.example/v1"):
    (providers_dir / f"{slug}.yaml").write_text(
        PROVIDER_BODY.format(slug=slug, base_url=base_url), encoding="utf-8"
    )


def test_a_new_provider_file_is_registered_by_a_reload(providers_dir):
    _write(providers_dir)
    # The cached scan predates the file, which is the whole point of the cache.
    assert "e2e-gateway" not in custom_provider_slugs()

    reload_custom_providers()

    assert "e2e-gateway" in custom_provider_slugs()
    info = provider_catalog.get("e2e-gateway")
    assert info is not None
    assert info.base_url == "https://gw.example/v1"
    # It is a real catalog entry, not just a count: the model picker reads it.
    assert any(p.slug == "e2e-gateway" for p in provider_catalog.list_providers())


def test_a_deleted_provider_file_leaves_the_catalog(providers_dir):
    _write(providers_dir)
    reload_custom_providers()
    assert "e2e-gateway" in custom_provider_slugs()

    (providers_dir / "e2e-gateway.yaml").unlink()
    reload_custom_providers()

    assert "e2e-gateway" not in custom_provider_slugs()
    assert provider_catalog.get("e2e-gateway") is None


def test_an_edited_base_url_lands_on_reload(providers_dir):
    _write(providers_dir)
    reload_custom_providers()

    _write(providers_dir, base_url="https://gw2.example/v1")
    reload_custom_providers()

    assert provider_catalog.get("e2e-gateway").base_url == "https://gw2.example/v1"


def test_a_deleted_provider_leaves_the_dynamic_fetcher(providers_dir):
    """Custom providers are mirrored into the dynamic model fetcher so they show
    up in ``/model``; a deleted file has to stop being offered there too."""
    from vtx.ai.dynamic_models import DYNAMIC_PROVIDERS

    _write(providers_dir)
    reload_custom_providers()
    assert "e2e-gateway" in DYNAMIC_PROVIDERS

    (providers_dir / "e2e-gateway.yaml").unlink()
    reload_custom_providers()

    assert "e2e-gateway" not in DYNAMIC_PROVIDERS


def test_a_removed_file_leaves_no_duplicate_when_it_returns(providers_dir):
    _write(providers_dir)
    reload_custom_providers()
    (providers_dir / "e2e-gateway.yaml").unlink()
    reload_custom_providers()
    _write(providers_dir, base_url="https://gw3.example/v1")
    reload_custom_providers()

    assert custom_provider_slugs().count("e2e-gateway") == 1
    assert provider_catalog.get("e2e-gateway").base_url == "https://gw3.example/v1"
