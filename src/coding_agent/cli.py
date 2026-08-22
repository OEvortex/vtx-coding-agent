import argparse
import asyncio
import os
import sys

from ai import PROVIDER_API_BY_NAME
from coding_agent.config import config
from coding_agent.version import VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vtx")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "update", help="Self-update vtx to the latest stable PyPI release and exit"
    )

    install_parser = subparsers.add_parser(
        "install", help="Install a vtx extension or agent package"
    )
    install_parser.add_argument(
        "name", help="Extension or agent package name (tries vtx-<name> then <name>)"
    )
    install_parser.add_argument(
        "--upgrade", action="store_true", help="Upgrade if already installed"
    )

    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall a vtx extension")
    uninstall_parser.add_argument("name", help="Extension package name to uninstall")

    subparsers.add_parser("list-extensions", help="List installed extensions")

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
        "--list-extensions", action="store_true", help="List installed extensions and exit"
    )
    parser.add_argument("--version", action="version", version=f"vtx {VERSION}")
    return parser


def main() -> None:
    parser = build_parser()

    # Handle subcommands before full parsing.
    if len(sys.argv) > 1 and sys.argv[1] in ("install", "uninstall", "list-extensions"):
        sub = sys.argv[1]

        if sub == "install":
            from ai.agent.extension_manager import install_extension

            name = sys.argv[2] if len(sys.argv) > 2 else None
            if not name:
                parser.error("install requires a package name")
            upgrade = "--upgrade" in sys.argv
            ok, msg, _ = install_extension(name, upgrade=upgrade)
            if ok:
                print(f"vtx install: {msg}")
            else:
                print(f"vtx install failed: {msg}", file=sys.stderr)
                raise SystemExit(1)
            raise SystemExit(0)

        if sub == "uninstall":
            from ai.agent.extension_manager import uninstall_extension

            name = sys.argv[2] if len(sys.argv) > 2 else None
            if not name:
                parser.error("uninstall requires a package name")
            ok, msg = uninstall_extension(name)
            if ok:
                print(f"vtx uninstall: {msg}")
            else:
                print(f"vtx uninstall failed: {msg}", file=sys.stderr)
                raise SystemExit(1)
            raise SystemExit(0)

        if sub == "list-extensions":
            from ai.agent.extension_manager import list_installed

            extensions = list_installed()
            if not extensions:
                print("No installed extensions.")
            else:
                for ext in extensions:
                    version = f" ({ext.version})" if ext.version else ""
                    print(f"{ext.name}{version}  source={ext.source}")
                    if ext.extensions:
                        print(f"  extensions: {', '.join(ext.extensions)}")
                    if ext.agents:
                        print(f"  agents: {', '.join(ext.agents)}")
            raise SystemExit(0)

    args = parser.parse_args()

    if args.command == "update":
        from coding_agent.self_update import self_update

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
        from ai.agent.agents import load_all_agents

        loaded, errors = load_all_agents(cwd=os.getcwd(), configured=args.agent_files)
        if not loaded and not errors:
            print("No agents found.")
        else:
            for a in loaded:
                print(f"{a.definition.name}\t{a.definition.description}\t{a.path}")
        for err in errors:
            print(f"agent error: {err}", file=sys.stderr)
        raise SystemExit(0)

    if args.list_extensions:
        from ai.agent.extension_manager import list_installed

        extensions = list_installed()
        if not extensions:
            print("No installed extensions.")
        else:
            for ext in extensions:
                version = f" ({ext.version})" if ext.version else ""
                print(f"{ext.name}{version}  source={ext.source}")
        raise SystemExit(0)

    if args.prompt is not None:
        from ai.agent.extensions import load_for_runtime
        from coding_agent.headless import run_headless

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
                )
            )
        )

    from tui.launch import run_tui

    run_tui(args)


if __name__ == "__main__":
    main()
