"""vx CLI: same flags as ctx, branded entrypoint, own TUI."""

from __future__ import annotations

import argparse
import os
import sys

from vx.brand import PROGRAM_NAME


def build_parser() -> argparse.ArgumentParser:
    # Deferred: provider catalog pulls the ai provider graph.
    from ctx.ai.provider_catalog import list_providers

    parser = argparse.ArgumentParser(prog=PROGRAM_NAME, description="vx")
    parser.add_argument("--model", "-m", help="Model to use")
    parser.add_argument(
        "--provider", choices=sorted(p.slug for p in list_providers()), help="Provider to use"
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
    parser.add_argument("--version", action="version", version=f"{PROGRAM_NAME} 0.1.0")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.prompt is not None and (args.continue_recent or args.resume_session):
        parser.error("-c/--continue and -r/--resume are not supported with -p/--prompt")

    from ctx.core.config import config

    if args.insecure_skip_verify:
        config.llm.tls.insecure_skip_verify = True

    # Importing ctx.code registers the coding tool rows and the base prompt.
    import ctx.code  # noqa: F401
    from ctx.plugins import boot as boot_plugins

    try:
        boot_plugins(
            args.profile,
            args.plugin_patches,
            cwd=os.getcwd(),
            extension_paths=[
                *config.extensions,
                *list(getattr(args, "extension_paths", None) or []),
            ],
            auto_discover_extensions=not getattr(args, "no_extensions", False),
        )
    except Exception as exc:
        print(f"{PROGRAM_NAME}: plugin composition failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.prompt is not None:
        from ctx.code.headless import run_headless
        from ctx.plugins.extensions import load_for_runtime

        loaded = load_for_runtime(
            cwd=os.getcwd(),
            extra_paths=[*config.extensions, *args.extension_paths],
            auto_discover=not args.no_extensions,
        )
        for err in loaded.errors:
            print(f"extension error: {err}", file=sys.stderr)

        import asyncio

        raise SystemExit(
            asyncio.run(
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
                )
            )
        )

    import asyncio

    from vx.app import VxApp

    app = VxApp(
        cwd=os.getcwd(),
        model=args.model,
        provider=args.provider,
        api_key=args.api_key,
        base_url=args.base_url,
        resume_session=args.resume_session,
        continue_recent=args.continue_recent,
        openai_compat_auth_mode=args.openai_compat_auth,
        anthropic_compat_auth_mode=args.anthropic_compat_auth,
    )
    raise SystemExit(asyncio.run(app.run()))


if __name__ == "__main__":
    main()
