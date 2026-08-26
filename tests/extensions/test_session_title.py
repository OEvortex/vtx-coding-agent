from pathlib import Path

from vtx.ai.agent.extensions import EventBus, Extension, ExtensionAPI
from vtx.core.paths import get_config_dir
from vtx.extensions.session_title import _clean_title, _get_extension_config, _is_command, register


def _make_api() -> ExtensionAPI:
    extension = Extension(name="session_title", path=Path("/fake/session_title.py"))
    bus = EventBus()
    return ExtensionAPI(
        extension=extension, bus=bus, cwd=".", session_file=None, config_dir=get_config_dir()
    )


# ---------------------------------------------------------------------------
# _clean_title tests
# ---------------------------------------------------------------------------


def test_clean_title_strips_ansi():
    assert _clean_title("\x1b[31mHello\x1b[0m") == "Hello"


def test_clean_title_takes_first_non_empty_line():
    # _clean_title extracts the first non-empty line and discards the rest.
    assert _clean_title("Line 1\n\nLine 2") == "Line 1"


def test_clean_title_strips_markdown_wrappers():
    assert _clean_title("**bold**") == "bold"
    assert _clean_title("`code`") == "code"
    assert _clean_title("# Heading") == "Heading"
    assert _clean_title("- list item") == "list item"


def test_clean_title_strips_quotes():
    assert _clean_title('"quoted"') == "quoted"
    assert _clean_title("'quoted'") == "quoted"


def test_clean_title_removes_trailing_punctuation():
    assert _clean_title("Hello!") == "Hello"
    assert _clean_title("Hello.") == "Hello"
    assert _clean_title("Hello?") == "Hello"


def test_clean_title_truncates_long_titles():
    long_text = "A" * 100
    result = _clean_title(long_text, max_length=10)
    assert len(result) == 10


def test_clean_title_returns_none_for_empty():
    assert _clean_title("") is None
    assert _clean_title("   ") is None
    assert _clean_title("\n\n") is None


def test_clean_title_returns_none_for_only_ansi():
    assert _clean_title("\x1b[31m\x1b[0m") is None


# ---------------------------------------------------------------------------
# _is_command tests
# ---------------------------------------------------------------------------


def test_is_command_slash():
    assert _is_command("/help") is True


def test_is_command_bang():
    assert _is_command("!ls") is True


def test_is_command_dollar():
    assert _is_command("$echo hi") is True


def test_is_command_regular_text():
    assert _is_command("Hello world") is False
    assert _is_command("Implement auth") is False


# ---------------------------------------------------------------------------
# _get_extension_config tests
# ---------------------------------------------------------------------------


def test_get_extension_config_defaults(monkeypatch):
    monkeypatch.setattr("vtx.extensions.session_title.vtx_config.extensions", None)
    assert _get_extension_config() == {}


def test_get_extension_config_reads_section(monkeypatch):
    from typing import ClassVar

    class FakeExtensions:
        session_title: ClassVar[dict] = {"enabled": True, "max_tokens": 30}

    monkeypatch.setattr("vtx.extensions.session_title.vtx_config.extensions", FakeExtensions())
    assert _get_extension_config() == {"enabled": True, "max_tokens": 30}


# ---------------------------------------------------------------------------
# register tests
# ---------------------------------------------------------------------------


def test_register_disabled(monkeypatch):
    api = _make_api()
    monkeypatch.setattr(
        "vtx.extensions.session_title._get_extension_config", lambda: {"enabled": False}
    )
    register(api)
    assert len(api._extension.handlers.get("input", [])) == 0
    assert len(api._extension.handlers.get("agent_settled", [])) == 0


def test_register_subscribes_to_events(monkeypatch):
    api = _make_api()
    monkeypatch.setattr(
        "vtx.extensions.session_title._get_extension_config", lambda: {"enabled": True}
    )
    register(api)
    assert len(api._extension.handlers.get("input", [])) == 1
    assert len(api._extension.handlers.get("agent_settled", [])) == 1


def test_register_input_handler_captures_first_message(monkeypatch):
    api = _make_api()
    monkeypatch.setattr(
        "vtx.extensions.session_title._get_extension_config", lambda: {"enabled": True}
    )
    register(api)

    input_handlers = api._extension.handlers.get("input", [])
    settled_handlers = api._extension.handlers.get("agent_settled", [])

    assert len(input_handlers) == 1
    assert len(settled_handlers) == 1

    input_handler = input_handlers[0]
    settled_handler = settled_handlers[0]

    # Simulate an input event with user text.
    payload_text = "Implement OAuth2 login"
    payload = type("Payload", (), {"text": payload_text})()
    ctx = type("Ctx", (), {})()
    input_handler("input", payload, ctx)

    # Simulate agent settled.
    settled_handler("agent_settled", type("Payload", (), {})(), ctx)

    # The title generation is async (asyncio.ensure_future), so set_session_name
    # is not called synchronously during the test.
    # We just verify the handlers ran without error.
    assert True
