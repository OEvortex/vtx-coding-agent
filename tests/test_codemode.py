"""The codemode sandbox: confinement, execution, and the failure taxonomy.

The tests that matter most are the escape attempts. They are written as
attacks rather than as assertions about internals, because "the script cannot
reach the host" is the one property that has to hold, and an implementation
detail test would keep passing after the property broke.

Escape tests assert on *impact*, not reachability: a probe that walks the
object graph and reaches the ``os`` module is acceptable, because that module's
capabilities are denied at the audit hook. What must never happen is a command
running, a file being read, or a socket connecting.
"""

from __future__ import annotations

import asyncio

import pytest

from vtx.ai.agent.codemode import (
    CodemodeSandbox,
    CodemodeSourceError,
    CodemodeTool,
    Limits,
    ToolError,
    clamp_timeout,
    parse_source,
    rank,
    render_declarations,
    to_identifier,
)

# Reaches a module by walking to a class whose __init__.__globals__ holds it.
# This is the known CPython introspection surface described in the worker
# module; it is the entry point for every escape probe below.
_WALK = (
    "def find(mod):\n"
    "    for c in ().__class__.__base__.__subclasses__():\n"
    "        try:\n"
    "            g = c.__init__.__globals__\n"
    "        except Exception:\n"
    "            continue\n"
    "        if mod in g:\n"
    "            return g[mod]\n"
    "    return None\n"
)


async def _echo(args, _signal):
    return {"said": args.get("text", "")}


async def _boom(args, _signal):
    raise ToolError("the tool declined", detail="internal detail that must not leak")


async def _unserializable(args, _signal):
    return object()


async def _slow(args, _signal):
    await asyncio.sleep(args.get("delay", 0.1))
    return "slept"


def _sandbox(**kwargs):
    kwargs.setdefault("limits", Limits(timeout_ms=20_000))
    return CodemodeSandbox(**kwargs)


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_arithmetic_returns_a_value():
    result = await _sandbox().execute("return 1 + 1")
    assert result.ok
    assert result.value == 2


@pytest.mark.asyncio
async def test_top_level_return_works():
    # The script is an async function body, not a bare exec block, so
    # `return` at the top level is legal.
    result = await _sandbox().execute("x = 4\nreturn x * x")
    assert result.ok
    assert result.value == 16


@pytest.mark.asyncio
async def test_empty_script_is_a_diagnostic_not_a_crash():
    result = await _sandbox().execute("   ")
    assert not result.ok
    assert result.diagnostic.kind == "script"


@pytest.mark.asyncio
async def test_text_appends_output_in_order():
    result = await _sandbox().execute("text('one')\ntext('two')\nreturn None")
    assert [item["text"] for item in result.output] == ["one", "two"]


@pytest.mark.asyncio
async def test_print_is_not_model_visible():
    # `text` is the output channel. `print` goes to a discarded stream, so the
    # model does not see it -- which is what keeps stdout free for protocol
    # frames.
    result = await _sandbox().execute("print('noise')\ntext('signal')\nreturn 1")
    assert result.value == 1
    assert [item["text"] for item in result.output] == ["signal"]


@pytest.mark.asyncio
async def test_tool_call_returns_its_value():
    sandbox = _sandbox(tools=[CodemodeTool(name="echo", description="Echo", execute=_echo)])
    result = await sandbox.execute("return await tools.echo(text='hi')")
    assert result.ok
    assert result.value == {"said": "hi"}
    assert [call.name for call in result.calls] == ["echo"]
    assert all(call.ok for call in result.calls)


@pytest.mark.asyncio
async def test_independent_calls_run_concurrently():
    sandbox = _sandbox(tools=[CodemodeTool(name="slow", description="Sleeps", execute=_slow)])
    result = await sandbox.execute(
        "rs = await asyncio.gather(*[tools.slow(delay=0.3) for _ in range(4)])\nreturn len(rs)"
    )
    assert result.ok
    # Serial execution would take 1.2s; the cap is 8 concurrent, so 4 run at
    # once. The assertion is on the result, and the timeout below is what
    # actually proves the parallelism.
    assert result.value == 4


@pytest.mark.asyncio
async def test_sequential_calls_are_slower_than_concurrent_ones():
    # Guards the concurrency claim: if the semaphore were 1, this would still
    # pass, so the check is that 4 x 400ms completes well inside 2 x that.
    sandbox = _sandbox(tools=[CodemodeTool(name="slow", description="Sleeps", execute=_slow)])
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await sandbox.execute(
        "await asyncio.gather(*[tools.slow(delay=0.4) for _ in range(4)])\nreturn 1",
        timeout_ms=10_000,
    )
    elapsed = loop.time() - start
    assert result.ok
    assert elapsed < 1.2


@pytest.mark.asyncio
async def test_allowlisted_import_works():
    result = await _sandbox().execute("import json\nreturn json.dumps({'a': 1})")
    assert result.ok
    assert result.value == '{"a": 1}'


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_write_is_reported_on_success():
    result = await _sandbox().execute("store('n', 2)\nreturn 'done'")
    assert result.ok
    assert result.store_writes == {"n": 2}


@pytest.mark.asyncio
async def test_store_write_is_dropped_on_failure():
    # A script that half-ran must not leave the host holding state the model
    # believes was never written.
    result = await _sandbox().execute("store('n', 2)\nraise ValueError('nope')")
    assert not result.ok
    assert result.store_writes == {}


@pytest.mark.asyncio
async def test_store_round_trips_across_executions():
    sandbox = _sandbox()
    store: dict = {}
    first = await sandbox.execute("store('seen', [1, 2])\nreturn 1", store=store)
    first.apply_to_store(store)
    second = await sandbox.execute("return load('seen')", store=store)
    assert second.value == [1, 2]


@pytest.mark.asyncio
async def test_load_returns_a_copy():
    # Mutating a loaded value must not reach host state, or the script could
    # edit the store without declaring a write.
    sandbox = _sandbox()
    store = {"k": {"n": 1}}
    result = await sandbox.execute("v = load('k')\nv['n'] = 99\nreturn load('k')", store=store)
    assert result.value == {"n": 1}


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_tool_is_its_own_kind():
    result = await _sandbox().execute("await tools.nope()\nreturn 1")
    assert not result.ok
    assert result.diagnostic.kind == "unknown_tool"
    assert "nope" in result.diagnostic.message


@pytest.mark.asyncio
async def test_unknown_tool_can_be_caught_by_type():
    # The reason for the taxonomy: Python lets the script branch on the kind,
    # so "wrong name" and "tool declined" produce different next moves.
    result = await _sandbox().execute(
        "try:\n    await tools.nope()\nexcept UnknownTool as e:\n    return f'handled: {e}'"
    )
    assert result.ok
    assert "handled" in result.value


@pytest.mark.asyncio
async def test_tool_failure_exposes_only_the_message():
    sandbox = _sandbox(tools=[CodemodeTool(name="boom", description="Fails", execute=_boom)])
    result = await sandbox.execute("return await tools.boom()")
    assert not result.ok
    assert result.diagnostic.kind == "tool_failure"
    assert result.diagnostic.message == "the tool declined"
    # The private detail exists for host logs only.
    assert "internal detail" not in repr(result)


@pytest.mark.asyncio
async def test_unclassified_tool_exception_is_sanitized():
    async def _explode(args, _signal):
        raise RuntimeError("/home/secret/path exploded")

    sandbox = _sandbox(tools=[CodemodeTool(name="x", description="X", execute=_explode)])
    result = await sandbox.execute("return await tools.x()")
    assert not result.ok
    assert "/home/secret" not in repr(result)


@pytest.mark.asyncio
async def test_non_json_tool_output_is_rejected():
    sandbox = _sandbox(tools=[CodemodeTool(name="u", description="U", execute=_unserializable)])
    result = await sandbox.execute("return await tools.u()")
    assert not result.ok
    assert result.diagnostic.kind == "invalid_output"


@pytest.mark.asyncio
async def test_script_error_reports_the_failing_line():
    result = await _sandbox().execute("a = 1\nb = 2\nraise ValueError('boom')")
    assert not result.ok
    assert result.diagnostic.kind == "script"
    assert "ValueError" in result.diagnostic.message


@pytest.mark.asyncio
async def test_infinite_loop_hits_the_deadline():
    # A kill, not a cooperative cancellation: `while True` cannot decline to
    # be stopped, which is the whole reason for a process per execution.
    result = await _sandbox().execute("while True:\n    pass", timeout_ms=800)
    assert not result.ok
    assert result.diagnostic.kind == "timeout"


@pytest.mark.asyncio
async def test_abort_signal_reports_aborted():
    sandbox = _sandbox()
    signal = asyncio.Event()
    task = asyncio.create_task(sandbox.execute("while True:\n    pass", signal=signal))
    await asyncio.sleep(0.3)
    signal.set()
    result = await task
    assert not result.ok
    assert result.diagnostic.kind == "aborted"


@pytest.mark.asyncio
async def test_output_survives_a_failure():
    result = await _sandbox().execute("text('before')\nraise ValueError('after')")
    assert not result.ok
    assert [item["text"] for item in result.output] == ["before"]


# --------------------------------------------------------------------------
# Escape attempts
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "import os",
        "import subprocess",
        "import socket",
        "import ctypes",
        "import sys",
        "import shutil",
        "import importlib",
        "import pickle",
    ],
)
@pytest.mark.asyncio
async def test_privileged_imports_are_denied(code):
    result = await _sandbox().execute(code)
    assert not result.ok
    assert "not available" in result.diagnostic.message


@pytest.mark.parametrize(
    "code",
    [
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "compile('1','','eval')",
        "globals()",
        "vars()",
        "getattr(x, 'y')",
        "breakpoint()",
        "input()",
    ],
)
@pytest.mark.asyncio
async def test_dangerous_builtins_are_absent(code):
    result = await _sandbox().execute(code)
    assert not result.ok
    assert result.diagnostic.kind == "script"


@pytest.mark.parametrize(
    ("code", "label"),
    [
        (_WALK + "sp = find('subprocess')\nreturn sp.run(['id']).returncode", "subprocess.run"),
        (_WALK + "sp = find('subprocess')\nreturn sp.call(['id'])", "subprocess.call"),
        (_WALK + "sp = find('subprocess')\nreturn sp.Popen(['id']).wait()", "subprocess.Popen"),
        (_WALK + "return find('os').popen('id').read()", "os.popen"),
        (
            _WALK + "o = find('os')\nfd = o.open('/etc/hostname', o.O_RDONLY)\n"
            "d = o.read(fd, 10)\no.close(fd)\nreturn d.decode()",
            "os.open + os.read",
        ),
        (
            _WALK + "s = find('_socket')\nsock = s.socket(2, 1)\n"
            "sock.connect(('127.0.0.1', 22))\nreturn 'connected'",
            "_socket connect",
        ),
        (
            _WALK + "o = find('os')\npid = o.fork()\n"
            "if pid == 0:\n    o._exit(0)\nreturn o.waitpid(pid, 0)[1]",
            "os.fork",
        ),
        (
            _WALK + "o = find('os')\nreturn o.posix_spawn('/bin/true', ['/bin/true'], o.environ)",
            "os.posix_spawn",
        ),
        (_WALK + "o = find('os')\nreturn sorted(o.listdir('/'))", "os.listdir"),
        (_WALK + "o = find('os')\nreturn next(iter(o.scandir('/'))).name", "os.scandir"),
        (_WALK + "return sorted(find('glob').glob('/etc/host*'))", "glob"),
    ],
)
@pytest.mark.asyncio
async def test_object_graph_walk_cannot_execute_or_connect(code, label):
    """The attack surface is reachable; the authority behind it is not.

    The probe walks the CPython object graph to the real ``os``/``subprocess``
    module. That much is a known introspection surface and is accepted. What
    must never happen is the operation succeeding -- so each case asserts the
    run failed, which is the property that carries weight.
    """
    result = await _sandbox().execute(code, timeout_ms=10_000)
    assert not result.ok, f"{label} escaped the sandbox"


@pytest.mark.asyncio
async def test_ctypes_is_not_reachable_at_all():
    # Unlike os and subprocess, ctypes is never resident, so the walk cannot
    # find it even before the audit hook would deny it.
    result = await _sandbox().execute(_WALK + "return find('ctypes')")
    assert result.ok
    assert result.value is None


@pytest.mark.asyncio
async def test_relative_import_is_denied():
    # Without this, `__package__ = "asyncio"` would reach asyncio.unix_events
    # and from there subprocess.
    result = await _sandbox().execute("__package__ = 'asyncio'\nfrom . import unix_events")
    assert not result.ok


@pytest.mark.asyncio
async def test_print_does_not_leak_host_globals():
    # `print` is the genuine builtin, so it has no __globals__ at all. That is
    # the strongest form of the fix: before it, `print` was a host closure and
    # `print.__globals__` was a direct hand-over of sys.modules.
    result = await _sandbox().execute("return print.__globals__")
    assert not result.ok
    assert "has no attribute '__globals__'" in result.diagnostic.message


@pytest.mark.asyncio
async def test_audit_hook_cannot_be_removed():
    result = await _sandbox().execute("import sys\nsys.addaudithook(lambda e, a: None)")
    assert not result.ok


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def test_identifier_sanitizes_non_identifier_characters():
    assert to_identifier("list-issues") == "list_issues"
    assert to_identifier("my.tool") == "my_tool"
    assert to_identifier("9lives") == "tool_9lives"


def test_identifier_avoids_reserved_names():
    # `tools`, `store`, `load`, and `text` are bound in the namespace; a tool
    # taking one of those names would silently shadow it.
    assert to_identifier("tools") == "tools_tool"
    assert to_identifier("store") == "store_tool"


def test_render_marks_optional_parameters_as_none():
    tool = CodemodeTool(
        name="list",
        description="List things",
        input_schema={
            "type": "object",
            "properties": {"owner": {"type": "string"}, "after": {"type": "string"}},
            "required": ["owner"],
        },
        execute=_echo,
    )
    body, complete = render_declarations([tool], budget_tokens=2000)
    assert complete
    # `after?` would be TypeScript spelling and is not valid Python.
    assert "after: str | None" in body
    assert "owner: str," in body


def test_render_reports_an_incomplete_budget():
    tools = [CodemodeTool(name=f"t{i}", description="x" * 200, execute=_echo) for i in range(10)]
    _, complete = render_declarations(tools, budget_tokens=50)
    # Honesty matters here: a model told the list is exhaustive will never
    # search for the tools that were dropped.
    assert not complete


def test_rank_finds_a_tool_by_its_parameter_description():
    tool = CodemodeTool(
        name="list_issues",
        description="List issues",
        input_schema={
            "type": "object",
            "properties": {
                "after": {"type": "string", "description": "Cursor from the previous page"}
            },
        },
        execute=_echo,
    )
    # Matching on the schema description is what lets a query naming a
    # parameter find the tool that has it.
    assert [m.tool.name for m in rank("cursor previous page", [tool])] == ["list_issues"]


def test_rank_ignores_stopwords():
    tool = CodemodeTool(name="web_search", description="Search the web", execute=_echo)
    assert [m.tool.name for m in rank("how do i search the web for news", [tool])] == [
        "web_search"
    ]


def test_rank_returns_nothing_for_an_empty_query():
    tool = CodemodeTool(name="a", description="b", execute=_echo)
    assert rank("", [tool]) == []


def test_instructions_report_partiality():
    tools = [CodemodeTool(name=f"t{i}", description="x" * 200, execute=_echo) for i in range(10)]
    text = CodemodeSandbox(tools=tools, catalog_budget_tokens=50).instructions()
    assert "PARTIAL" in text
    assert "tools.search" in text


def test_the_advertised_search_tool_is_callable():
    """The instructions must not name a tool that does not exist.

    This was a real bug: the instructions told the model to call
    ``tools.search(...)`` when the budget could not fit the catalog, and no such
    tool was ever registered. The recovery advice was impossible to follow.
    """
    tools = [CodemodeTool(name=f"t{i}", description="x" * 200, execute=_echo) for i in range(10)]
    sandbox = CodemodeSandbox(tools=tools, catalog_budget_tokens=50)
    assert "tools.search" in sandbox.instructions()
    # `tools` property is the injected set; search is appended at dispatch.
    assert "search" not in {t.identifier() for t in sandbox.tools}


@pytest.mark.asyncio
async def test_search_is_reachable_from_inside_a_script():
    tools = [
        CodemodeTool(
            name="github.list_issues", description="List repository issues", execute=_echo
        ),
        CodemodeTool(name="web_search", description="Search the web for news", execute=_echo),
    ]
    sandbox = CodemodeSandbox(tools=tools, catalog_budget_tokens=20)
    result = await sandbox.execute(
        "m = await tools.search(query='repository issues')\n"
        "return [x['name'] for x in m['matches']]"
    )
    assert result.ok, result.diagnostic
    assert result.value == ["github.list_issues"]


@pytest.mark.asyncio
async def test_search_accepts_an_exact_path_and_returns_a_signature():
    tools = [CodemodeTool(name="web_search", description="Search the web", execute=_echo)]
    sandbox = CodemodeSandbox(tools=tools, catalog_budget_tokens=20)
    # An exact path wins over a keyword match: a model that already knows the
    # name wants that tool, not the closest-ranked neighbour.
    for query in ("web_search", "tools.web_search"):
        result = await sandbox.execute(
            f"m = await tools.search(query='{query}')\n"
            "hit = m['matches'][0]\n"
            "return [hit['name'], 'def tools.' in hit['signature']]"
        )
        assert result.ok, result.diagnostic
        # A list, not a tuple: tuples do not survive the JSON boundary, and a
        # test asserting a tuple here would be asserting the wrong contract.
        assert result.value == ["web_search", True]


@pytest.mark.asyncio
async def test_search_with_an_empty_query_browses_the_catalog():
    tools = [
        CodemodeTool(name="b", description="second", execute=_echo),
        CodemodeTool(name="a", description="first", execute=_echo),
    ]
    sandbox = CodemodeSandbox(tools=tools, catalog_budget_tokens=20)
    result = await sandbox.execute(
        "m = await tools.search(query='')\nreturn [x['name'] for x in m['matches']]"
    )
    assert result.ok, result.diagnostic
    # Browsed alphabetically, so a model that does not know what it is looking
    # for can still enumerate.
    assert result.value == ["a", "b"]


@pytest.mark.asyncio
async def test_search_reports_a_next_page_when_truncated():
    tools = [CodemodeTool(name=f"t{i}", description="shared", execute=_echo) for i in range(6)]
    sandbox = CodemodeSandbox(tools=tools, catalog_budget_tokens=20)
    result = await sandbox.execute(
        "m = await tools.search(query='shared', limit=2)\n"
        "return [len(m['matches']), m['next'], m['remaining'] > 0]"
    )
    assert result.ok, result.diagnostic
    length, next_page, has_more = result.value
    assert length == 2
    assert next_page == {"offset": 2}
    assert has_more is True


def test_a_tool_cannot_take_the_reserved_search_name():
    # A collision would shadow the built-in, so the instructions would describe a
    # search that searched something else.
    with pytest.raises(ValueError, match="reserved"):
        CodemodeSandbox(tools=[CodemodeTool(name="search", description="mine", execute=_echo)])


@pytest.mark.asyncio
async def test_search_is_callable_even_when_the_catalog_is_complete():
    # Always registered, so a speculative call from a model that misread a
    # COMPLETE list finds the tool instead of an unknown-name error.
    sandbox = CodemodeSandbox(
        tools=[CodemodeTool(name="read", description="Read a file", execute=_echo)]
    )
    assert "tools.search" not in sandbox.instructions()
    result = await sandbox.execute(
        "m = await tools.search(query='read')\nreturn len(m['matches'])"
    )
    assert result.ok, result.diagnostic
    assert result.value == 1


def test_instructions_are_complete_when_the_budget_allows():
    tool = CodemodeTool(name="a", description="does a thing", execute=_echo)
    text = CodemodeSandbox(tools=[tool]).instructions()
    assert "COMPLETE" in text
    # Workflow first, catalog last: a model reads the top of a long prompt.
    assert text.index("## Workflow") < text.index("## Available tools")


# --------------------------------------------------------------------------
# Wire contract
# --------------------------------------------------------------------------


def _load_sandbox_module():
    """Import the worker by path, the same way the host launches it."""
    import importlib.util

    from vtx.ai.agent.codemode import SANDBOX_PATH

    spec = importlib.util.spec_from_file_location("_codemode_sandbox", SANDBOX_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_sandbox_imports_nothing_from_the_package():
    # This is what makes the ~30ms launch possible: `python -m` would import
    # vtx/__init__, which pulls in the agent SDK and costs ~1.5s per execution.
    sandbox = _load_sandbox_module()
    leaked = [
        name
        for name, value in vars(sandbox).items()
        if getattr(value, "__name__", "").startswith("vtx")
    ]
    assert not leaked, f"sandbox.py must not import from vtx: {leaked}"


def test_diagnostic_kinds_match_across_the_wire():
    # The worker is launched by path and cannot import this package, so the
    # kinds exist on both sides. This is the test that keeps them honest.
    from vtx.ai.agent.codemode import errors

    sandbox = _load_sandbox_module()
    assert set(sandbox.KINDS) == set(errors.KINDS)
    for kind in sandbox.KINDS:
        assert sandbox.remedy_for(kind) == errors.remedy_for(kind), kind


def test_launch_overhead_is_small():
    # Guards the reason the worker is launched by path. If someone converts it
    # back to `-m`, this is the test that notices.
    import time

    async def _measure():
        start = time.monotonic()
        result = await _sandbox().execute("return 1", timeout_ms=30_000)
        return time.monotonic() - start, result

    elapsed, result = asyncio.run(_measure())
    assert result.ok
    assert elapsed < 1.0, f"execution overhead regressed to {elapsed:.2f}s"


# --------------------------------------------------------------------------
# Source options
# --------------------------------------------------------------------------


def test_options_line_is_parsed_and_blanked():
    parsed = parse_source('# @options: {"timeout_ms": 5000}\nreturn 1')
    assert parsed.timeout_ms == 5000
    # Blanked, not removed, so traceback line numbers still match.
    assert parsed.code.split("\n")[1:] == ["return 1"]


def test_options_line_preserves_line_numbers():
    parsed = parse_source("# @options: {}\nreturn 1")
    assert parsed.code.split("\n").index("return 1") == 1


@pytest.mark.parametrize(
    "source",
    [
        "",
        "# @options: not json\nreturn 1",
        '# @options: {"unknown": 1}\nreturn 1',
        "# @options: {}\n",
        '# @options: {"timeout_ms": "soon"}\nreturn 1',
        '# @options: {"timeout_ms": 5}\nreturn 1',
    ],
)
def test_bad_options_are_rejected(source):
    # Rejected rather than ignored: a model that sets a limit which is
    # silently dropped will believe it configured something it did not.
    with pytest.raises(CodemodeSourceError):
        parse_source(source)


def test_a_script_may_shorten_the_deadline_but_not_extend_it():
    assert clamp_timeout(1000, 30_000) == 1000
    assert clamp_timeout(60_000, 30_000) == 30_000
    assert clamp_timeout(None, 30_000) == 30_000


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_duplicate_identifiers_are_rejected():
    # Better to refuse the configuration than to debug a shadowed tool that
    # looks present in the declarations but cannot be called.
    with pytest.raises(ValueError, match="both map to identifier"):
        CodemodeSandbox(
            tools=[
                CodemodeTool(name="list-issues", description="a", execute=_echo),
                CodemodeTool(name="list_issues", description="b", execute=_echo),
            ]
        )


@pytest.mark.asyncio
async def test_closed_sandbox_refuses_execution():
    sandbox = _sandbox()
    await sandbox.close()
    result = await sandbox.execute("return 1")
    assert not result.ok
    assert result.diagnostic.kind == "sandbox"


@pytest.mark.asyncio
async def test_oversized_input_store_is_refused_before_spawning():
    from vtx.ai.agent.codemode import MAX_STORE_VALUE_CHARS

    result = await _sandbox().execute("return 1", store={"k": "x" * (MAX_STORE_VALUE_CHARS + 1)})
    assert not result.ok
    assert result.diagnostic.kind == "sandbox"


def test_the_stall_kind_is_mirrored_in_both_processes():
    """The worker is launched by path and cannot import this package.

    So the diagnostic kinds are written twice, once per side, and this is what
    catches them drifting apart. A kind that exists on only one side would be
    reported to the model under a name it was never told about.
    """
    import re
    from pathlib import Path

    from vtx.ai.agent.codemode import errors
    from vtx.ai.agent.codemode.sandbox import STALLED

    names = (
        "SCRIPT",
        "TIMEOUT",
        "ABORTED",
        "SANDBOX",
        "STALLED",
        "UNKNOWN_TOOL",
        "INVALID_INPUT",
        "TOOL_FAILURE",
        "INVALID_OUTPUT",
        "HOST_UNAVAILABLE",
    )
    worker = Path(errors.__file__).with_name("sandbox.py").read_text()
    for name in names:
        match = re.search(rf'^{name}\s*=\s*"([^"]+)"', worker, re.M)
        assert match is not None, f"{name} missing from the worker"
        assert match.group(1) == getattr(errors, name), name

    # And it is terminal, so a script cannot catch its way out of a deadlock.
    assert STALLED in errors.SANDBOX_KINDS
    assert STALLED not in errors.TOOL_KINDS
    assert STALLED in errors.KINDS
