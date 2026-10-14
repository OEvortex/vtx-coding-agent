import argparse
import asyncio
import os
import sys
from typing import Any

# ``vtx.ai.providers`` rather than ``vtx.ai``: the barrel also imports the
# provider oauth clients (aiohttp), which this only needs the API-type catalog
# from. Importing the barrel here cost ~165ms on every startup.
from vtx.ai.providers import PROVIDER_API_BY_NAME
from vtx.core.config import config
from vtx.core.version import VERSION


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Vtx")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "update", help="Self-update vtx to the latest stable PyPI release and exit"
    )

    plugin_parser = subparsers.add_parser(
        "plugin", help="Manage plugin profiles, bundles, and rows"
    )
    plugin_sub = plugin_parser.add_subparsers(dest="plugin_command")
    plugin_sub.add_parser("profiles", help="List known profiles and bundles")
    plugin_sub.add_parser(
        "init", help="Create a profile directory with an empty patch layer"
    ).add_argument("name", metavar="NAME", help="Profile name")
    plugin_sub.add_parser("path", help="Print a profile's directory").add_argument(
        "name", metavar="NAME", help="Profile name"
    )
    enable_cmd = plugin_sub.add_parser("enable", help="Enable a row in the profile patch")
    enable_cmd.add_argument("row_id", metavar="ROW_ID")
    disable_cmd = plugin_sub.add_parser("disable", help="Disable a row in the profile patch")
    disable_cmd.add_argument("row_id", metavar="ROW_ID")
    install_cmd = plugin_sub.add_parser(
        "install", help="Install a plugin package from PyPI or a git URL"
    )
    install_cmd.add_argument("spec", metavar="PACKAGE_OR_URL")
    install_cmd.add_argument("--upgrade", action="store_true")
    allow_cmd = plugin_sub.add_parser(
        "allow-version", help="Permit one exact package@version on one exact vtx version"
    )
    allow_cmd.add_argument("target", metavar="PACKAGE@VERSION")
    allow_cmd.add_argument("--vtx-version", required=True, metavar="VERSION")
    allow_cmd.add_argument("--accept-risk", action="store_true")
    revoke_cmd = plugin_sub.add_parser("revoke-version", help="Revoke a version exemption")
    revoke_cmd.add_argument("target", metavar="PACKAGE@VERSION")
    revoke_cmd.add_argument("--vtx-version", required=True, metavar="VERSION")
    plugin_sub.add_parser("version-exemptions", help="List this profile's version exemptions")

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
        "--list-extensions",
        action="store_true",
        help="List the extensions this composition loaded, then exit",
    )
    parser.add_argument(
        "--profile",
        default=None,
        metavar="NAME",
        help="Plugin profile to boot (default: 'default'; see `vtx plugin profiles`)",
    )
    parser.add_argument(
        "--patch",
        action="append",
        default=[],
        dest="plugin_patches",
        metavar="PATH",
        help="Apply a plugin patch layer after the profile's own layer (repeatable)",
    )
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help="Print the composed plugin row list for the selected profile and exit",
    )
    parser.add_argument("--version", action="version", version=f"vtx {VERSION}")
    return parser


def _print_loaded_extensions(host: Any) -> int:
    """Print what extension discovery contributed to the mounted composition."""
    from vtx.plugins.extensions import load_for_runtime

    loaded = load_for_runtime(cwd=os.getcwd())
    rows = loaded.describe()
    if not rows:
        print("No extensions loaded.")
    for row in rows:
        print(f"{row['name']}  {row['path']}")
        for kind in ("tools", "commands", "handlers"):
            if row[kind]:
                print(f"  {kind}: {', '.join(row[kind])}")
    for err in loaded.errors:
        print(f"extension error: {err}", file=sys.stderr)
    host.dispose()
    return 0


def main() -> None:
    parser = build_parser()

    # Handle subcommands before full parsing.
    if len(sys.argv) > 1 and sys.argv[1] == "plugin":
        from vtx.coding_agent.plugin_cmd import run_plugin_command

        raise SystemExit(run_plugin_command(sys.argv[2:]))

    args = parser.parse_args()

    if args.command == "update":
        from vtx.core.self_update import self_update

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
        from vtx.coding_agent.agents import load_all_agents

        loaded, errors = load_all_agents(cwd=os.getcwd(), configured=args.agent_files)
        if not loaded and not errors:
            print("No agents found.")
        else:
            for a in loaded:
                print(f"{a.definition.name}\t{a.definition.description}\t{a.path}")
        for err in errors:
            print(f"agent error: {err}", file=sys.stderr)
        raise SystemExit(0)

    from vtx.plugins import boot as boot_plugins
    from vtx.plugins.profile import DEFAULT_PROFILE, dump_config

    if args.dump_config:
        profile = args.profile or DEFAULT_PROFILE
        records = dump_config(profile, args.plugin_patches)
        # Extension rows are discovered at boot, not stored in the profile, so
        # they are not in ``records``. Print them too, or this listing would
        # under-report a tree whose headline claim is that everything is a row.
        from vtx.plugins.extensions import ExtensionService

        discovered = ExtensionService().load(
            cwd=os.getcwd(),
            auto_discover=not getattr(args, "no_extensions", False),
            extra_paths=[*config.extensions, *list(getattr(args, "extension_paths", None) or [])],
            entry_points=False,
        )
        for spec in discovered:
            records.append(
                {
                    "id": spec.name,
                    "name": spec.meta.get("path", spec.name),
                    "disabled": False,
                    "config": {},
                }
            )
        for row in records:
            state = "disabled" if row.get("disabled") else "enabled"
            print(f"{row['id']}\t[{state}]\t{row['name']}")
        raise SystemExit(0)

    try:
        # ``config.extensions`` is the user's ``~/.vtx/config.yml`` list; it was
        # merged in by the runtime callers before discovery moved here, and the
        # composition is now the only place extensions are discovered.
        host = boot_plugins(
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
        print(f"vtx: plugin composition failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.list_extensions:
        raise SystemExit(_print_loaded_extensions(host))

    if args.prompt is not None:
        from vtx.coding_agent.headless import run_headless
        from vtx.plugins.extensions import load_for_runtime

        loaded = load_for_runtime(
            cwd=os.getcwd(),
            extra_paths=[*config.extensions, *args.extension_paths],
            auto_discover=not args.no_extensions,
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

    from vtx.tui.launch import run_tui

    run_tui(args)


if __name__ == "__main__":
    main()
