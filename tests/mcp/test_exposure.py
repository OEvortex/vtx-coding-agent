"""The exposure taxonomy: who can reach an MCP tool, and how.

The value here is in the edges, not the happy path. A taxonomy that resolves the
obvious case correctly and then, say, lets an exact-name override lose to a
pattern, or resolves a group of servers to its narrowest member, is worse than no
taxonomy at all -- because the configuration reads as if it were obeyed.
"""

from __future__ import annotations

import pytest

from vtx.mcp.exposure import (
    DECLARED_TO_MODEL,
    EXPOSURES,
    SCRIPT_CALLABLE,
    needs_script_reach,
    normalize_exposure,
    tool_exposure,
    validate_exposures,
    widest,
)


def test_codemode_is_the_default_exposure():
    # The default matters more than any other value here. A server with nothing
    # configured used to be invisible to scripts, and the only alternative was to
    # declare every tool to the model, which does not scale past a dozen.
    assert tool_exposure(None, {}, "search") == "codemode"


def test_a_server_exposure_applies_to_all_its_tools():
    assert tool_exposure("direct", {}, "search") == "direct"
    assert tool_exposure("hidden", {}, "anything") == "hidden"


def test_an_exact_override_beats_the_server_and_the_patterns():
    overrides = {"search": "direct", "get_*": "hidden"}
    assert tool_exposure("codemode", overrides, "search") == "direct"


def test_a_pattern_override_beats_the_server_exposure():
    assert tool_exposure("direct", {"delete_*": "hidden"}, "delete_repo") == "hidden"
    # ...and does not touch its neighbours.
    assert tool_exposure("direct", {"delete_*": "hidden"}, "get_repo") == "direct"


def test_the_first_matching_pattern_wins():
    # Declaration order is the documented tiebreak, and it is a real footgun
    # worth pinning: `{"*": ..., "delete_*": ...}` resolves deletes by the FIRST
    # match, which is `*`. A user who writes that and means "everything except
    # deletes" gets the opposite, so the specific pattern has to come first.
    catch_all_first = {"*": "codemode", "delete_*": "hidden"}
    assert tool_exposure(None, catch_all_first, "delete_repo") == "codemode"

    specific_first = {"delete_*": "hidden", "*": "codemode"}
    assert tool_exposure(None, specific_first, "delete_repo") == "hidden"
    assert tool_exposure(None, specific_first, "read_repo") == "codemode"


def test_a_single_tool_can_opt_out_of_a_hidden_server():
    # The shape that makes a narrow server usable: hide everything, then name
    # the few tools that are actually wanted.
    overrides = {"*": "hidden", "search": "codemode"}
    assert tool_exposure("hidden", overrides, "search") == "codemode"
    assert tool_exposure("hidden", overrides, "write_file") == "hidden"


def test_hidden_means_a_pattern_can_never_resurrect_it():
    overrides = {"write_*": "hidden", "*": "codemode"}
    assert tool_exposure(None, overrides, "write_file") == "hidden"


# ---- validation ----------------------------------------------------------


def test_an_unknown_exposure_is_rejected_rather_than_coerced():
    assert normalize_exposure("direct") == "direct"
    assert normalize_exposure("Direct") is None
    assert normalize_exposure(True) is None
    _, _, error = validate_exposures("sideways", None)
    assert error is not None
    assert "exposure must be one of" in error


def test_saying_nothing_about_exposure_is_not_an_error():
    # A server that omits it is the common case, not a malformed one.
    exposure, overrides, error = validate_exposures(None, None)
    assert (exposure, overrides, error) == (None, {}, None)


def test_a_malformed_tool_exposure_is_reported():
    _, _, error = validate_exposures(None, {"search": "sideways"})
    assert error is not None and "search" in error
    _, _, error = validate_exposures(None, {"search": "direct", "": "hidden"})
    assert error is not None


# ---- grouping ------------------------------------------------------------


def test_the_widest_exposure_wins_for_a_group_of_servers():
    # The MCP resource tools front every connected server at once. Collapsing to
    # the narrowest would hide a capability a `direct` server genuinely offers.
    assert widest("direct", "codemode") == "direct"
    assert widest("codemode", "deferred") == "codemode"
    assert widest("hidden", "hidden") == "hidden"
    assert widest() == "hidden"


def test_only_script_reachable_exposures_need_a_script():
    # This is the condition for `codemode` being worth having at all: a session
    # whose servers are all `direct` gains nothing from it.
    assert needs_script_reach(["codemode"])
    assert needs_script_reach(["deferred"])
    assert not needs_script_reach(["direct", "hidden"])
    assert not needs_script_reach([])


def test_every_exposure_is_a_known_string():
    # Guards against a value being added to one place and not another.
    for value in EXPOSURES:
        assert normalize_exposure(value) == value


@pytest.mark.parametrize("bad", [None, 1, [], {}, "codemode "])
def test_normalize_rejects_non_exposures(bad):
    assert normalize_exposure(bad) is None


def test_the_package_re_exports_what_a_host_needs():
    """A TUI filter or a profile check should not have to reach into a submodule.

    The names are re-exported so a host making its own exposure decision reads
    the same taxonomy the runtime does, rather than re-deriving it and drifting.
    """
    import vtx.mcp as mcp

    missing = [name for name in mcp.__all__ if not hasattr(mcp, name)]
    assert missing == []

    assert mcp.EXPOSURES == EXPOSURES
    assert mcp.SCRIPT_CALLABLE == SCRIPT_CALLABLE
    assert mcp.DECLARED_TO_MODEL == DECLARED_TO_MODEL
    # A built-in has no exposure and is declared, whatever else changes.
    assert mcp.declared_to_model(None) is True
    assert mcp.declared_to_model("codemode") is False


# ---- patterns must survive the config parser -----------------------------


def test_a_wildcard_key_is_accepted_by_the_parser():
    """`{"delete_*": "hidden"}` is the documented way to make a broad server
    usable, so a validator that rejects it as a malformed key makes the
    documentation a lie. The whole resolution machinery is unreachable if the
    only way to spell a pattern is refused at the front door.
    """
    from vtx.mcp.config import validate_mcp_server_config

    config, error = validate_mcp_server_config(
        "docs",
        {
            "url": "https://example.com/mcp",
            "exposure": "codemode",
            "tool_exposure": {"delete_*": "hidden", "search": "direct"},
        },
    )
    assert error is None, error
    assert config is not None
    assert config.exposure_of("delete_repo") == "hidden"
    assert config.exposure_of("search") == "direct"
    assert config.exposure_of("read_repo") == "codemode"


@pytest.mark.parametrize("key", ["*", "delete_*", "get_?", "get_[a-z]*", "a-b", "x1"])
def test_valid_pattern_keys_are_accepted(key):
    from vtx.mcp.config import validate_mcp_server_config

    config, error = validate_mcp_server_config(
        "s", {"command": "x", "tool_exposure": {key: "hidden"}}
    )
    assert error is None, (key, error)
    assert config is not None and config.tool_exposure == {key: "hidden"}


@pytest.mark.parametrize("key", ["has space", "a.b", "quote'", "semi;colon"])
def test_keys_that_are_neither_a_name_nor_a_pattern_are_rejected(key):
    from vtx.mcp.config import validate_mcp_server_config

    config, error = validate_mcp_server_config(
        "s", {"command": "x", "tool_exposure": {key: "hidden"}}
    )
    assert config is None
    assert error is not None and "tool name or pattern" in error
