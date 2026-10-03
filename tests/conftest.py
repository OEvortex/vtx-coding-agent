import pytest

from vtx.agent.dispatcher import set_context
from vtx.agent.tools import get_tool
from vtx.core.config import get_config, reset_config


def pytest_runtest_setup(item):
    # Auto-approve so existing tests aren't blocked by permission prompts
    get_config()._parsed.permissions.mode = "auto"


class FakeChat:
    """Reusable minimal chat sink for UI tests."""

    def __init__(self) -> None:
        self.compaction_tokens: int | None = None
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.launch_warnings: list[object] = []
        self.statuses: list[str] = []
        self.versions: list[str] = []
        self.changelog_urls: list[str | None] = []

    def add_compaction_message(self, tokens_before: int, tokens_after: int = 0) -> None:
        self.compaction_tokens = tokens_before

    def start_compaction(self, **_kwargs) -> None:
        return None

    def update_compaction_progress(self, _chars: int, _sections: list) -> None:
        return None

    def finish_compaction(self, *, tokens_before: int = 0, **_kwargs) -> None:
        self.compaction_tokens = tokens_before

    def add_info_message(self, message: str, error: bool = False, warning: bool = False) -> None:
        if error:
            self.errors.append(message)
        elif warning:
            self.warnings.append(message)
        else:
            self.infos.append(message)

    def add_update_available_message(
        self, latest_version: str, changelog_url: str | None = None
    ) -> None:
        self.versions.append(latest_version)
        self.changelog_urls.append(changelog_url)

    def add_launch_warnings(self, warnings) -> None:
        self.launch_warnings.extend(warnings)

    def show_status(self, message: str) -> None:
        self.statuses.append(message)


@pytest.fixture(autouse=True)
def isolate_user_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # core.paths.get_config_dir() prefers os.environ["HOME"] over
    # Path.home(), so both must point at the SAME isolated dir or the
    # developer's real ~/.vtx leaks into config-dependent tests.
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    reset_config()
    # The dispatcher context must be cleared: it is a plain module global, not a
    # contextvar, so a test that installs a live session leaves it installed for
    # every later test in the same xdist worker. Without this reset the suite's
    # pass/fail depends on how xdist happens to shard it.
    yield
    set_context(None)
    # Same class of leak, same reason. `codemode` is a registry singleton that a
    # runtime re-points at its own tool list, permission config, and extension
    # bus. A test that builds a runtime and does not close it leaves those
    # installed, and a later test asserting on the built-in catalog then sees
    # that runtime's tools instead.
    codemode = get_tool("codemode")
    if codemode is not None:
        codemode.tool_source = None
        codemode.extensions = None
        codemode.permission = None
        codemode.cancel_event = None
        codemode.refresh()


@pytest.fixture
def fake_chat() -> FakeChat:
    return FakeChat()


def pytest_runtest_teardown(item, nextitem):
    reset_config()
