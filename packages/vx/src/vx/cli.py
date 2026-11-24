"""vx CLI: same flags as ctx, branded entrypoint, own TUI."""

from __future__ import annotations

import argparse
import os
import sys

from vx.brand import PROGRAM_NAME, VERSION


def validate_provider(parser: argparse.ArgumentParser, value: str) -> None:
    """Check ``--provider`` post-parse, in argparse's own wording.

    ``choices=`` cannot be used: argparse joins the whole choice list into the
    help text (``_expand_help``), which pulled in the provider catalog (a ~330ms
    YAML + aiohttp import) for ``--version`` and ``--help`` too. Validating here
    means the catalog only loads when a provider is actually named.
    """
    from ctx.ai.provider_catalog import list_providers

    slugs = sorted({p.slug for p in list_providers()})
    if value not in slugs:
        choices = "".join(f"'{s}', " for s in slugs).rstrip(", ")
        parser.error(f"argument --provider: invalid choice: {value!r} (choose from {choices})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, description="vx")
    parser.add_argument("--model", "-m", help="Model to use")
    parser.add_argument(
        "--provider", metavar="PROVIDER", help="Provider to use (see `ctx providers list`)"
    )
    parser.add_argument(
        "--prompt",
        "-p",
        nargs="?",
        const="-",
        default=None,
        help="Run a single prompt non-interactively, then exit "
        "(omit the value or pipe stdin to read the prompt from stdin)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="With -p/--prompt, print each tool call to stderr"
    )
    parser.add_argument("--api-key", "-k", help="API key")
    parser.add_argument("--base-url", "-u", help="Base URL for API")
    parser.add_argument(
        "--openai-compat-auth",
        choices=("auto", "required", "none"),
        help="Auth mode for OpenAI-compatible endpoints",
    )
    parser.add_argument(
        "--anthropic-compat-auth",
        choices=("auto", "required", "none"),
        help="Auth mode for Anthropic-compatible endpoints",
    )
    parser.add_argument(
        "--insecure-skip-verify",
        action="store_true",
        help="Skip TLS verification (e.g. self-signed certs on local providers)",
    )
    parser.add_argument(
        "--continue",
        "-c",
        action="store_true",
        dest="continue_recent",
        help="Resume the most recent session",
    )
    parser.add_argument(
        "--resume",
        "-r",
        dest="resume_session",
        help="Resume a specific session by ID (full or unique prefix)",
    )
    parser.add_argument(
        "--extension",
        "-e",
        action="append",
        default=[],
        dest="extension_paths",
        metavar="PATH",
        help="Load a Python extension file or package from PATH (repeatable)",
    )
    parser.add_argument(
        "--no-extensions",
        action="store_true",
        help="Skip auto-discovered extensions in .ctx/extensions/ and ~/.ctx/agent/extensions/",
    )
    parser.add_argument(
        "--agent",
        "-a",
        default=None,
        metavar="NAME",
        help="Activate a handoff agent at session start (name of a .ctx/agent/<name>.py)",
    )
    parser.add_argument(
        "--agent-file",
        action="append",
        default=[],
        dest="agent_files",
        metavar="PATH",
        help="Load an additional agent file or package from PATH (repeatable)",
    )
    parser.add_argument(
        "--no-agents",
        action="store_true",
        help="Skip auto-discovered agents in .ctx/agent/ and ~/.ctx/agent/",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="Plugin profile to boot (default: 'default'; see `ctx plugin profiles`)",
    )
    parser.add_argument(
        "--patch",
        action="append",
        default=[],
        dest="plugin_patches",
        metavar="PATH",
        help="Apply a plugin patch layer after the profile's own layer (repeatable)",
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM_NAME} {VERSION}")
    return parser


def main() -> None:
    argv = sys.argv[1:]
    # argparse's version action needs the parser; printing the bundled string
    # first keeps `vx --version` free of every ctx import.
    if argv and all(arg in ("--version", "-V") for arg in argv):
        print(f"{PROGRAM_NAME} {VERSION}")
        return

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.provider:
        validate_provider(parser, args.provider)

    if args.prompt is not None and (args.continue_recent or args.resume_session):
        parser.error("-c/--continue and -r/--resume are not supported with -p/--prompt")

    if args.prompt is not None:
        raise SystemExit(_run_headless(args))
    raise SystemExit(_run_tui(args))


class BootError(RuntimeError):
    """Startup failed before the UI took over; the caller prints it."""


def _boot(argv_args: argparse.Namespace, *, cwd: str) -> None:
    """Load the ctx plugin graph. Everything here costs a few hundred ms."""
    from ctx.core.config import config

    if argv_args.insecure_skip_verify:
        config.llm.tls.insecure_skip_verify = True

    # vx contributes its own persona: the harness base ships no prompt content
    # of its own.
    from vx import prompts as vx_prompts

    vx_prompts.register()
    from ctx.plugins import boot as boot_plugins

    try:
        boot_plugins(
            argv_args.profile,
            argv_args.plugin_patches,
            cwd=cwd,
            extension_paths=[
                *config.extensions,
                *list(getattr(argv_args, "extension_paths", None) or []),
            ],
            auto_discover_extensions=not getattr(argv_args, "no_extensions", False),
        )
    except Exception as exc:
        raise BootError(f"plugin composition failed: {exc}") from exc


def _run_headless(args: argparse.Namespace) -> int:
    import asyncio

    cwd = os.getcwd()
    try:
        _boot(args, cwd=cwd)
    except BootError as exc:
        print(f"{PROGRAM_NAME}: {exc}", file=sys.stderr)
        return 1

    from ctx.core.config import config
    from ctx.plugins.extensions import load_for_runtime

    from vx.headless import run_headless

    loaded = load_for_runtime(
        cwd=cwd,
        extra_paths=[*config.extensions, *args.extension_paths],
        auto_discover=not args.no_extensions,
    )
    for err in loaded.errors:
        print(f"extension error: {err}", file=sys.stderr)

    return asyncio.run(
        run_headless(
            prompt_arg=args.prompt,
            model=args.model,
            provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            openai_compat_auth_mode=args.openai_compat_auth,
            anthropic_compat_auth_mode=args.anthropic_compat_auth,
            loaded_extensions=loaded,
            active_agent_name=args.agent,
            agent_files=args.agent_files,
            auto_discover_agents=not args.no_agents,
            verbose=args.verbose,
        )
    )


def _run_tui(args: argparse.Namespace) -> int:
    import asyncio

    cwd = os.getcwd()

    # Paint the shell before ctx loads, so the terminal is never blank.
    from vx import prefs
    from vx.tui import splash
    from vx.tui.terminal import Terminal

    term = Terminal()
    term.enter()
    # The tty is entered here, before the app exists, so the pointer preference
    # is applied here too: off keeps the terminal's drag-select.
    term.set_mouse(prefs.mouse_capture())
    try:
        painter = splash.paint(term, cwd=cwd)
        _boot(args, cwd=cwd)

        from vx.app import VxApp

        app = VxApp(
            cwd=cwd,
            model=args.model,
            provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            resume_session=args.resume_session,
            continue_recent=args.continue_recent,
            openai_compat_auth_mode=args.openai_compat_auth,
            anthropic_compat_auth_mode=args.anthropic_compat_auth,
            terminal=term,
            painter=painter,
        )
    except BootError as exc:
        # The message belongs on the normal screen, not the alternate one.
        term.exit()
        print(f"{PROGRAM_NAME}: {exc}", file=sys.stderr)
        return 1
    except BaseException:
        term.exit()
        raise
    return asyncio.run(app.run())


if __name__ == "__main__":
    main()
