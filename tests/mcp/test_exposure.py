"""The exposure taxonomy: who can reach an MCP tool, and how.

The value here is in the edges, not the happy path. A taxonomy that resolves the
obvious case correctly and then, say, lets an exact-name override lose to a
pattern, or resolves a group of servers to its narrowest member, is worse than no
taxonomy at all -- because the configuration reads as if it were obeyed.
"""

from __future__ import annotations

import pytest

from vtx.mcp.exposure import (
    EXPOSURES,
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
