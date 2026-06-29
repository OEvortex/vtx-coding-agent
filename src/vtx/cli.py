import argparse
import asyncio
import os
import sys

from vtx import config

from .llm import PROVIDER_API_BY_NAME
from .version import VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vtx")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "update", help="Self-update vtx to the latest stable PyPI release and exit"
    )
    parser.add_argument("--model", "-m", help="Model to use")
    parser.add_argument("--provider", choices=sorted(PROVIDER_API_BY_NAME), help="Provider to use")
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
        help="Skip auto-discovered extensions in .vtx/extensions/ and ~/.vtx/agent/extensions/",
    )
    parser.add_argument(
        "--agent",
        "-a",
        default=None,
        metavar="NAME",
        help="Activate a handoff agent at session start (name of a .vtx/agent/<name>.py)",
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
        help="Skip auto-discovered agents in .vtx/agent/ and ~/.vtx/agent/",
    )
    parser.add_argument(
        "--list-agents", action="store_true", help="List all available agents and exit"
    )
    parser.add_argument(
        "--goal",
        default=None,
        metavar="OBJECTIVE",
        help="Set a completion goal before the run (see /goal command).",
    )
    parser.add_argument("--version", action="version", version=f"vtx {VERSION}")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "update":
        from .self_update import self_update

        ok, message = self_update()
        if ok:
            print(f"vtx update: {message}")
        else:
            print(f"vtx update failed: {message}", file=sys.stderr)
            raise SystemExit(1)
        raise SystemExit(0)

    if args.prompt is not None and (args.continue_recent or args.resume_session):
        parser.error("-c/--continue and -r/--resume are not supported with -p/--prompt")

    if args.insecure_skip_verify:
        config.llm.tls.insecure_skip_verify = True

    if args.list_agents:
        from .agents import load_all_agents

        loaded, errors = load_all_agents(cwd=os.getcwd(), configured=args.agent_files)
        if not loaded and not errors:
            print("No agents found.")
        else:
            for a in loaded:
                print(f"{a.definition.name}\t{a.definition.description}\t{a.path}")
        for err in errors:
            print(f"agent error: {err}", file=sys.stderr)
        raise SystemExit(0)

    if args.prompt is not None:
        from .extensions import load_for_runtime
        from .headless import run_headless

        loaded = load_for_runtime(
            cwd=os.getcwd(), extra_paths=args.extension_paths, auto_discover=not args.no_extensions
        )
        for err in loaded.errors:
            print(f"extension error: {err}", file=sys.stderr)

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
                    goal_objective=args.goal,
                )
            )
        )

    from .ui.launch import run_tui

    run_tui(args)


if __name__ == "__main__":
    main()
