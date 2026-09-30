"""Tests for the /harness browse/delete command and its argument parser.

The point of the command is that a user can see what the agent believes and
remove an entry they know is wrong, without spending a refinement pass and
without the model having to agree. So the behaviours worth pinning are: the
parser resolves the verbs and the scope flag, both scopes are visible, a delete
actually removes the entry from the store that holds it, and context is
reloaded so the model stops reading a digest that still mentions the entry.
"""

import pytest

from vtx.ai.agent.rlm import refine as refine_mod
from vtx.ai.agent.rlm.harness import get_harness_state
from vtx.ai.agent.rlm.refine import parse_harness_command_options, resolve_harness_entry
from vtx.ai.agent.rlm.registry import bridge_session_id, reset_state
from vtx.tui.autocomplete import DEFAULT_COMMANDS
from vtx.tui.commands import CommandsMixin


@pytest.fixture(autouse=True)
def clean_state():
    reset_state()
    yield
    reset_state()


class FakeChat:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.statuses: list[str] = []
        self.entry_lists: list[list] = []
        self.rich: list[object] = []

    def add_info_message(self, message: str, error: bool = False, warning: bool = False) -> None:
        if error:
            self.errors.append(message)
        elif warning:
            self.warnings.append(message)
        else:
            self.infos.append(message)

    def add_harness_entries(self, entries, *, title="", empty="") -> None:
        self.entry_lists.append(list(entries))
        self.last_title = title

    def add_rich_message(self, renderable, *, error: bool = False) -> None:
        self.rich.append(renderable)

    def show_status(self, message: str) -> None:
        self.statuses.append(message)


class FakeRuntime:
    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.session = object()
        self.reloads = 0

    def reload_context(self) -> None:
        self.reloads += 1


class FakeCommands(CommandsMixin):
    def __init__(self, cwd: str) -> None:
        self.chat = FakeChat()
        self._runtime = FakeRuntime(cwd)
        self._is_running = False

    def query_one(self, selector, widget_type):
        if selector == "#chat-log":
            return self.chat
        raise AssertionError(f"Unexpected selector: {selector}")


def _local(cwd: str):
    return get_harness_state(refine_mod.local_state_dir(bridge_session_id(), cwd))


# =================================================================================================
# argument parsing
# =================================================================================================


def test_bare_harness_lists_everything():
    options = parse_harness_command_options("")

    assert options.action == "list"
    assert options.target == ""
    assert options.global_ is False
    assert not options.errors


def test_global_flag_is_recognised_before_and_after_the_verb():
    before = parse_harness_command_options("--global list")
    after = parse_harness_command_options("list --global")

    assert before.global_ is after.global_ is True
    assert before.action == after.action == "list"


def test_a_bare_kind_is_a_filter_not_an_unknown_verb():
    """`/harness memory` should list memories, not error on an unknown action."""
    options = parse_harness_command_options("memory")

    assert options.action == "list"
    assert options.kind == "memory"
    assert options.target == ""


def test_trailing_kind_filters_a_list():
    options = parse_harness_command_options("list memory")

    assert options.kind == "memory"
    assert options.target == ""


def test_search_keeps_its_query_whole():
    options = parse_harness_command_options("search postgres pooling")

    assert options.action == "search"
    assert options.target == "postgres pooling"


def test_delete_requires_an_id():
    assert parse_harness_command_options("delete").errors


def test_show_requires_an_id():
    assert parse_harness_command_options("show").errors


def test_an_unknown_option_is_reported_rather_than_ignored():
    """Silently dropping a typo'd flag would act on the wrong scope."""
    options = parse_harness_command_options("delete my-entry --globl")

    assert options.errors
    assert "--globl" in options.errors[0]


def test_an_unknown_verb_is_reported():
    assert parse_harness_command_options("prune everything").errors


# =================================================================================================
# listing
# =================================================================================================


def test_list_shows_local_and_global_entries(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Local note", "Use pgbouncer locally.", id="local-note")
    get_harness_state(global_=True).create(
        "memory", "Global note", "Shared lesson.", id="global-note"
    )
    commands = FakeCommands(cwd)

    commands._handle_harness_command("list")

    ids = {entry.id for entry in commands.chat.entry_lists[0]}
    assert ids == {"local-note", "global-note"}


def test_global_flag_limits_the_listing_to_global_entries(tmp_path):
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Local note", "Local only.", id="local-note")
    get_harness_state(global_=True).create("memory", "Global note", "Shared.", id="global-note")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("list --global")

    assert {entry.id for entry in commands.chat.entry_lists[0]} == {"global-note"}


def test_a_colliding_id_in_both_scopes_is_listed_twice(tmp_path):
    """Same slug, different entries: hiding one would lose it from view."""
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Twin", "Local version.", id="shared")
    get_harness_state(global_=True).create("memory", "Twin", "Global version.", id="shared")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("list")

    assert len(commands.chat.entry_lists[0]) == 2


def test_a_kind_filter_excludes_the_other_kinds(tmp_path):
    cwd = str(tmp_path)
    state = _local(cwd)
    state.create("memory", "A memory", "content", id="a-memory")
    state.create("prompt", "A prompt", "content", id="a-prompt")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("list memory")

    assert {entry.kind for entry in commands.chat.entry_lists[0]} == {"memory"}


# =================================================================================================
# show
# =================================================================================================


def test_show_resolves_by_title_not_just_id(tmp_path):
    """The user is reading a list of titles, so a title is a valid handle."""
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Pooling note", "Use pgbouncer on 6432.", id="pooling-note")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("show Pooling note")

    assert not commands.chat.errors
    assert commands.chat.rich


def test_show_reports_a_miss_as_an_error(tmp_path):
    commands = FakeCommands(str(tmp_path))

    commands._handle_harness_command("show nothing-here")

    assert commands.chat.errors
    assert not commands.chat.rich


# =================================================================================================
# search
# =================================================================================================


def test_search_ranks_and_returns_matches(tmp_path):
    cwd = str(tmp_path)
    state = _local(cwd)
    state.create("memory", "Pooling", "Use pgbouncer on port 6432 for prod.", id="pooling")
    state.create("memory", "Unrelated", "Formatting preferences for markdown.", id="unrelated")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("search pgbouncer")

    assert {entry.id for entry in commands.chat.entry_lists[0]} == {"pooling"}


def test_search_with_no_hits_warns_rather_than_errors(tmp_path):
    """No match is a legitimate answer, not a failure."""
    commands = FakeCommands(str(tmp_path))

    commands._handle_harness_command("search nothingmatches")

    assert commands.chat.warnings
    assert not commands.chat.errors


def test_search_needs_a_query(tmp_path):
    commands = FakeCommands(str(tmp_path))

    commands._handle_harness_command("search")

    assert commands.chat.errors


# =================================================================================================
# delete
# =================================================================================================


def test_delete_removes_the_entry_from_the_store(tmp_path):
    cwd = str(tmp_path)
    state = _local(cwd)
    state.create("memory", "Wrong fact", "The port is 5432.", id="wrong-fact")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("delete wrong-fact")

    assert state.get("memory", "wrong-fact") is None
    assert commands.chat.infos


def test_delete_reloads_context_so_the_digest_stops_mentioning_it(tmp_path):
    """Otherwise the model keeps being told about a memory that no longer exists."""
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Wrong fact", "The port is 5432.", id="wrong-fact")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("delete wrong-fact")

    assert commands._runtime.reloads == 1


def test_delete_removes_a_global_entry_from_the_global_store(tmp_path):
    """Found through the merged view, but it must go from the store that holds it."""
    cwd = str(tmp_path)
    global_state = get_harness_state(global_=True)
    global_state.create("memory", "Bad global", "Wrong for every session.", id="bad-global")
    commands = FakeCommands(cwd)

    commands._handle_harness_command("delete bad-global")

    assert global_state.get("memory", "bad-global") is None
    assert commands._runtime.reloads == 1


def test_delete_of_a_missing_entry_errors_without_reloading(tmp_path):
    commands = FakeCommands(str(tmp_path))

    commands._handle_harness_command("delete ghost")

    assert commands.chat.errors
    assert commands._runtime.reloads == 0


def test_resolve_prefers_a_global_entry_over_a_local_one(tmp_path):
    """Both may exist; the global one is the one asserted to every session."""
    cwd = str(tmp_path)
    _local(cwd).create("memory", "Twin", "Local version.", id="twin")
    global_state = get_harness_state(global_=True)
    global_state.create("memory", "Twin", "Global version.", id="twin")

    found = resolve_harness_entry(bridge_session_id(), cwd, "twin")

    assert found is not None
    entry, _state = found
    assert entry.scope == "global"
    assert entry.content == "Global version."


# =================================================================================================
# discoverability
# =================================================================================================


def test_harness_is_in_the_slash_command_list():
    command = next((c for c in DEFAULT_COMMANDS if c.name == "harness"), None)

    assert command is not None
    assert "harness" in command.description
