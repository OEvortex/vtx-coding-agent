"""How an MCP tool is offered to the model: directly, through a script, or not at all.

Every MCP server now publishes a potentially large tool surface, and a session
with six servers connected can easily have two hundred tools. Declaring all of
them to the model on every turn is unaffordable, and declaring none of them
makes the integration worthless. So each tool gets an *exposure* -- a decision
about who can see it -- and this module is the whole taxonomy.

The values and their consequences:

``direct``
    Declared to the model as an ordinary tool call, and callable from a script.
    This is what an MCP server's tools looked like before any of this existed,
    so it stays available as an explicit choice for a server whose handful of
    tools the model should reason about directly.

``codemode``
    Callable from a script, and listed in the ``codemode`` tool's description.
    Not declared as a direct tool call. The model composes several of them in
    one turn for the price of one, and the intermediate results never enter the
    transcript. This is the default because it is what makes an MCP tool set
    usable at scale: a hundred tools that the model must call one at a time is a
    hundred turns.

``codemode-deferred``
    Callable from a script, *not* listed in the description. Reachable by
    guessing the name or via ``tools.search``, and nothing advertises it. For a
    server with a long tail of rarely-used tools, where listing them all would
    crowd out the ones that matter.

``deferred``
    Like ``codemode-deferred``, but the discovery path is the model-side
    ``tool_search`` tool rather than a script. For tools that should be loaded
    on demand when a model asks for one by name.

``hidden``
    Registered, but unreachable from both directions. The tombstone for a tool
    a server still lists but this session should not use -- a destructive
    tool on a server you use for reads, for instance.

The default for a server with nothing configured is ``codemode``. Before this
existed, an MCP tool the model could not call directly was invisible to
everything, and the only alternative was to declare everything, which does not
scale. ``codemode`` is the setting that gives a server's tools to the model at
a cost that does not grow with the tool count.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable
from typing import Final

#: Every exposure a server or a single tool may declare.
EXPOSURES: Final[tuple[str, ...]] = (
    "direct",
    "codemode",
    "codemode-deferred",
    "deferred",
    "hidden",
)

#: Exposures whose tools a script may call, whether or not they are listed.
#:
#: ``direct`` is in here because a tool the model can already call directly can
#: also be composed into a script -- a model that needs six of them in one turn
#: should not have to pay six. It is left *out* of :data:`SCRIPT_LISTED`, because
#: advertising it in the codemode catalog would describe something the model
#: already has in front of it.
SCRIPT_CALLABLE: Final = frozenset({"direct", "codemode", "codemode-deferred", "deferred"})

#: Exposures declared to the model as an ordinary tool call, which it may invoke
#: by name. Everything else is reachable only through a script or a search, and
#: staying out of the request is the point: a two-hundred-tool server declared
#: tool-by-tool costs two hundred definitions on every turn, which is the cost
#: the other exposures exist to avoid.
DECLARED_TO_MODEL: Final = frozenset({"direct"})

#: Exposures the ``codemode`` description advertises. ``codemode-deferred`` and
#: ``deferred`` are callable but unlisted, which is the whole difference.
SCRIPT_LISTED: Final = frozenset({"codemode"})

#: Exposures a script may also reach through the in-sandbox search tool. A
#: deferred tool is not listed but is still findable, or "deferred" would mean
#: "unfindable" and the only route left would be guessing the name.
SCRIPT_DISCOVERABLE: Final = frozenset({"codemode", "codemode-deferred", "deferred"})

#: Order used to pick one exposure for a resource tool that spans several
#: servers, and for collapsing the two deferred variants when reporting. Widest
#: first, so a group of servers never resolves to its narrowest member.
_BY_WIDTH: Final = ("direct", "codemode", "codemode-deferred", "deferred", "hidden")

#: Exposures that make ``codemode`` worth having in the first place. A session
#: whose servers are all ``direct`` or ``hidden`` gains nothing from it.
_SCRIPT_REACHING: Final = frozenset({"codemode", "codemode-deferred", "deferred"})

_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

#: A ``tool_exposure`` key is an exact tool name, or an ``fnmatch`` pattern over
#: one. The wildcards have to be allowed, or the documented
#: ``{"delete_*": "hidden"}`` -- the one setting that makes a broad server
#: usable with care -- is rejected as a malformed key.
_NAME_OR_PATTERN_RE = re.compile(r"\A[A-Za-z0-9_*?\[\]-]{1,64}\Z")


def normalize_exposure(value: object) -> str | None:
    """Return ``value`` as a known exposure, or ``None`` if it is not one.

    ``None`` means the configuration is wrong, and the caller reports it as
    such. A silently coerced value would leave a server exposed to the model in
    a way nobody wrote down.
    """
    return value if isinstance(value, str) and value in EXPOSURES else None


def validate_exposures(
    exposure: object, tool_exposure: object
) -> tuple[str | None, dict[str, str], str | None]:
    """Validate a server's exposure settings.

    Returns ``(exposure, tool_exposure, error)``. ``exposure`` is ``None`` when
    the server said nothing, which is not an error -- it means the default
    applies. One bad tool entry does not invalidate the server, because a
    server whose read tools work and whose one exotic tool is misconfigured is
    far more useful than no server.
    """
    if exposure is None:
        resolved: str | None = None
    else:
        resolved = normalize_exposure(exposure)
        if resolved is None:
            return None, {}, f"exposure must be one of {', '.join(EXPOSURES)}"

    overrides: dict[str, str] = {}
    if tool_exposure is None:
        return resolved, overrides, None
    if not isinstance(tool_exposure, dict):
        return resolved, {}, "tool_exposure must map tool names to exposures"

    for key, value in tool_exposure.items():
        if not isinstance(key, str) or not _NAME_OR_PATTERN_RE.match(key):
            return (
                resolved,
                {},
                (
                    f'tool_exposure key "{key}" is not a tool name or pattern '
                    "(letters, digits, _ - and the * ? [ ] wildcards)"
                ),
            )
        normalized = normalize_exposure(value)
        if normalized is None:
            return resolved, {}, (f'tool_exposure "{key}" must be one of {", ".join(EXPOSURES)}')
        overrides[key] = normalized
    return resolved, overrides, None


def tool_exposure(config_exposure: str | None, overrides: dict[str, str], tool_name: str) -> str:
    """Resolve one tool's exposure: its override, else the server's, else the default.

    An exact name beats a pattern; among patterns, the first in declaration
    order wins. That ordering is stated rather than left to dict luck, because
    ``{"delete_*": "hidden", "*": "codemode"}`` reads as "everything codemode
    except deletes" and would mean the opposite if iteration order flipped.
    """
    exact = overrides.get(tool_name)
    if exact is not None:
        return exact
    for pattern, value in overrides.items():
        if ("*" in pattern or "?" in pattern) and fnmatch.fnmatch(tool_name, pattern):
            return value
    return config_exposure or "codemode"


def is_valid_name(name: object) -> bool:
    return isinstance(name, str) and bool(_NAME_RE.match(name))


def declared_to_model(exposure: object) -> bool:
    """Whether a tool with this exposure is sent to the provider as a tool call.

    A tool with no exposure is a built-in, and built-ins are always declared.
    """
    return exposure is None or exposure in DECLARED_TO_MODEL


def widest(*exposures: str) -> str:
    """The exposure that reaches the most: ``direct`` beats ``codemode`` beats rest.

    Used for a tool that fronts several servers at once, like the MCP resource
    tools. Collapsing to the narrowest would hide a capability that one of the
    servers genuinely offers.
    """
    present = set(exposures)
    for candidate in _BY_WIDTH:
        if candidate in present:
            return candidate
    return "hidden"


def needs_script_reach(exposures: Iterable[str]) -> bool:
    """Whether any of these exposures is only reachable by running a script."""
    return any(value in _SCRIPT_REACHING for value in exposures)
