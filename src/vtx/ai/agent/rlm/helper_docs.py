"""The model-facing helper reference, generated rather than hand-typed.

The RLM prompt used to carry a hand-maintained signature table
(``_RLM_HELPERS_PROMPT``) listing every pre-bound helper. Nothing tied it to
``_init_builtin_helpers``: adding a helper without documenting it made it
invisible to the model, and changing a signature left the prompt advertising an
argument list the kernel no longer accepted. Both failure modes are silent.

So the reference is generated from :data:`HELPER_SPECS`, and
``tests/test_rlm_helper_reference.py`` asserts every declared signature against
``inspect.signature`` of the object the kernel actually binds. A changed
signature fails a test instead of misleading the model.

The non-callable names (``rlm``, ``harness``, ``context``, the ``In``/``Out``
history) are described in :data:`OBJECTS` instead: they have no signature to
generate, and inventing one would be a lie the model could act on.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HelperSpec:
    """One pre-bound helper: how the model calls it and what it is for."""

    #: The name the kernel binds in the REPL namespace.
    name: str
    #: The full call signature, as the model should write it. Verified against
    #: the bound object by the test suite, so it cannot drift.
    signature: str
    #: What the call returns.
    returns: str
    #: One line on what it is for and when to reach for it.
    summary: str
    #: Whether ``signature`` is the helper's own Python signature and can be
    #: checked against ``inspect.signature``. False for the one helper whose
    #: documented call form is a wrapper (``await host_request("<type>", {...})``
    #: is ``rlm.host_request``, not the bound ``host_request(data)``).
    verify_signature: bool = True


#: Every pre-bound helper, in the order the model should learn them: the ones it
#: reaches for constantly first, then the bridge and discovery helpers.
HELPER_SPECS: tuple[HelperSpec, ...] = (
    HelperSpec(
        name="bash",
        signature="bash(command)",
        returns="BashHandle",
        summary=(
            "starts a shell command and returns a live handle immediately; it NEVER "
            "blocks. Full handle API in the REPL control section above: h.output() / "
            "h.tail(n), h.poll(), h.kill(), await h"
        ),
    ),
    HelperSpec(
        name="run_bash",
        signature="run_bash(command, timeout=180)",
        returns="str",
        summary=(
            "blocking shell command returning combined stdout+stderr as a string "
            "(times out with a note; the command keeps running)"
        ),
    ),
    HelperSpec(
        name="read_file",
        signature="read_file(path, offset=0, limit=2000)",
        returns="str",
        summary="read a file slice (or list a directory)",
    ),
    HelperSpec(
        name="write_file",
        signature="write_file(path, content)",
        returns="None",
        summary="create or overwrite a file",
    ),
    HelperSpec(
        name="edit_file",
        signature="edit_file(path, old, new, replace_all=False)",
        returns="str",
        summary="exact search-and-replace edit; raises if `old` not found",
    ),
    HelperSpec(
        name="run_code",
        signature="run_code(code_str)",
        returns="Any",
        summary="execute a code string in the namespace, return its last expression value",
    ),
    HelperSpec(
        name="rerun",
        signature="rerun(index=-1)",
        returns="Any",
        summary="re-run a previous code snippet or cell by index",
    ),
    HelperSpec(
        name="find_tools",
        signature="find_tools(query, limit=8)",
        returns="list[dict]",
        summary=(
            "search the callable tool surface by keyword, best matches first. Use this "
            "when you need a capability this prompt does not name: it returns "
            "[{name, description}], and you call the winner with call_tool. "
            "Do not guess tool names"
        ),
    ),
    HelperSpec(
        name="describe_tool",
        signature="describe_tool(name)",
        returns="dict | None",
        summary=(
            "one tool's full declaration (name, description, parameters JSON schema), "
            "or None if the name is not callable. Pair it with find_tools to get the "
            "arguments right the first time"
        ),
    ),
    HelperSpec(
        name="web_search",
        signature="web_search(query, num_results=8)",
        returns="str",
        summary="web search via the tool bridge",
    ),
    HelperSpec(
        name="goal_get",
        signature="goal_get()",
        returns="dict",
        summary="focused-goal snapshot via the tool bridge",
    ),
    HelperSpec(
        name="goal_update",
        signature="goal_update(**kwargs)",
        returns="dict",
        summary='e.g. goal_update(status="complete", completion_summary="...")',
    ),
    HelperSpec(
        name="goal_set_tasks",
        signature="goal_set_tasks(tasks)",
        returns="dict",
        summary=(
            "replace the task plan; tasks is a list of {title, id?, parent_id?, note?} dicts"
        ),
    ),
    HelperSpec(
        name="call_tool",
        signature="call_tool(name, **kwargs)",
        returns="Any",
        summary=(
            'generic escape hatch to any main-process tool ("web_search", "goal", '
            '"task", ...). Find the exact name with find_tools and its arguments '
            "with describe_tool first"
        ),
    ),
    HelperSpec(
        name="emit",
        signature="emit(data)",
        returns="None",
        summary="ship one display event (dict of MIME type -> JSON payload) to the host",
    ),
    HelperSpec(
        name="host_request",
        signature='await host_request("<type>", {...})',
        returns="dict",
        summary=(
            "generic async host bridge used by Python skills; raises on a host error "
            "or unregistered type"
        ),
        # The documented form is ``rlm.host_request(type, payload)``, the
        # two-argument wrapper. The bound name is the one-argument
        # ``host_request(data)`` it delegates to, so the two differ on purpose and
        # must not be compared against each other.
        verify_signature=False,
    ),
)

#: Names bound in the namespace that are not callable, or whose call shape is not
#: a signature. Described separately because a generated signature would be
#: fiction.
OBJECTS: tuple[tuple[str, str], ...] = (
    (
        "rlm",
        "the model-facing namespace object (rlm.spawn, rlm.list_subagents, "
        "rlm.collect, rlm.delete_subagent, rlm.progress_note, rlm.find_models, "
        "rlm.harness, rlm.get_harness_state)",
    ),
    ("harness", "alias of `rlm.harness` for the continual harness store"),
    ("context", "the RLMContext object for this session (see above)"),
    ("In, Out, _i, _ii, _iii, _, __, ___, _oh", "IPython-style execution history variables"),
)

#: Names the guard in ``_handle_execute`` protects, stated in the prompt because a
#: model that rebinds one gets it silently restored.
SHADOWING_NOTICE = (
    "These names are bound to the helpers and cannot be reassigned. A cell that "
    "rebinds or deletes one has it restored and is told which names were restored, "
    "because the namespace is the same dict for the life of the kernel: a shadowed "
    "`call_tool` would silently disarm the tool bridge for every later cell. Pick a "
    "different name for your own binding."
)

_NOT_CALLABLE_NOTE = (
    "The `rlm` object is not callable: calling it raises TypeError `'rlm' is not "
    "callable; spawn a child with: handle = await rlm.spawn('sub-task', "
    "name=\\'worker\\')'`. There is no blocking foreground spawn — `rlm.spawn` returns "
    "at admission and never the answer."
)


def render_helpers_reference() -> str:
    """The ``# Pre-bound REPL helpers`` prompt section, generated from the specs."""
    lines = [
        "# Pre-bound REPL helpers",
        "",
        "These names already exist in the REPL namespace — call them directly, do not "
        "import or define them:",
        "",
    ]
    lines.extend(
        f"- `{spec.signature}` -> {spec.returns} : {spec.summary}" for spec in HELPER_SPECS
    )
    lines.extend(f"- `{name}` : {summary}" for name, summary in OBJECTS)
    lines.extend(["", SHADOWING_NOTICE, "", _NOT_CALLABLE_NOTE])
    return "\n".join(lines)


__all__ = ["HELPER_SPECS", "OBJECTS", "SHADOWING_NOTICE", "HelperSpec", "render_helpers_reference"]
