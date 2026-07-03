import subprocess
import sys

import pytest

from vtx.llm import BaseProvider, get_provider_class
from vtx.llm.models import ApiType


def _modules_loaded_after(import_target: str) -> set[str]:
    code = f"import {import_target}\nimport sys\nprint('\\n'.join(sys.modules))"
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return set(out.splitlines())


def _module_loaded(loaded: set[str], module_name: str) -> bool:
    return any(name == module_name or name.startswith(f"{module_name}.") for name in loaded)


@pytest.mark.parametrize("import_target", ["vtx.llm", "vtx.ui.app", "vtx.cli", "vtx.headless"])
def test_import_does_not_load_provider_sdks(import_target):
    loaded = _modules_loaded_after(import_target)
    assert not _module_loaded(loaded, "openai")
    assert not _module_loaded(loaded, "anthropic")


@pytest.mark.parametrize("import_target", ["vtx.cli", "vtx.headless"])
def test_import_does_not_load_textual(import_target):
    loaded = _modules_loaded_after(import_target)
    assert not _module_loaded(loaded, "textual")


_PROVIDER_CASES = [
    (ApiType(ApiType.OPENAI_SDK), "OpenAISDKProvider"),
    (ApiType(ApiType.ANTHROPIC), "AnthropicSDKProvider"),
    (ApiType(ApiType.OPENAI_COMPLETIONS), "OpenAISDKProvider"),
    (ApiType(ApiType.SUPERCODE), "SupercodeProvider"),
]


def test_provider_lookup_covers_all_api_types():
    provided = {api_type for api_type, _ in _PROVIDER_CASES}
    all_types = {ApiType(v) for v in ApiType._VALUES}
    assert provided == all_types


@pytest.mark.parametrize(("api_type", "class_name"), _PROVIDER_CASES)
def test_get_provider_class_resolves_registered_providers(api_type, class_name):
    cls = get_provider_class(api_type)
    assert cls.__name__ == class_name
    assert issubclass(cls, BaseProvider)
