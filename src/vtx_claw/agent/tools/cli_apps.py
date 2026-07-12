"""Controlled runner for installed CLI Apps."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from vtx_claw.agent.tools.base import Tool, tool_parameters
from vtx_claw.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from vtx_claw.apps.cli import CliAppError, CliAppManager, CliAppsRuntimeConfig
from vtx_claw.config_base import Base
from vtx_claw.security.workspace_access import current_tool_workspace


class CliAppsToolConfig(Base):
    """CLI Apps tool configuration."""

    enable: bool = True
    install_timeout: int = Field(default=300, ge=1, le=3600)
    run_timeout: int = Field(default=60, ge=1, le=600)
    catalog_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)


@tool_parameters(
    tool_parameters_schema(
        required=["name"],
        name=StringSchema(
            "Installed CLI app registry name (e.g. gimp, safari, obsidian)."
        ),
        args=ArraySchema(
            StringSchema("One command-line argument."),
            description="Args for the CLI entry point (not the entry point itself).",
            nullable=True,
        ),
        json=BooleanSchema(
            description="Prepend --json when the CLI supports it.",
            default=False,
            nullable=True,
        ),
        working_dir=StringSchema("Optional working directory.", nullable=True),
        timeout=IntegerSchema(
            description="Timeout seconds for this CLI call.",
            minimum=1,
            maximum=600,
            nullable=True,
        ),
    )
)
class CliAppsTool(Tool):
    """Run an installed CLI-Anything or public CLI app through a controlled argv subprocess."""

    config_key = "cli_apps"
    _scopes = {"core", "subagent"}

    @classmethod
    def config_cls(cls):
        return CliAppsToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.cli_apps.enable

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        cfg = ctx.config.cli_apps
        return cls(
            workspace=Path(ctx.workspace),
            restrict_to_workspace=ctx.config.restrict_to_workspace,
            runtime=CliAppsRuntimeConfig(
                install_timeout=cfg.install_timeout,
                run_timeout=cfg.run_timeout,
                catalog_ttl_seconds=cfg.catalog_ttl_seconds,
            ),
        )

    def __init__(
        self,
        *,
        workspace: Path,
        restrict_to_workspace: bool = False,
        runtime: CliAppsRuntimeConfig | None = None,
    ) -> None:
        self.workspace = workspace
        self.restrict_to_workspace = restrict_to_workspace
        self.runtime = runtime or CliAppsRuntimeConfig()

    @property
    def name(self) -> str:
        return "run_cli_app"

    @property
    def description(self) -> str:
        try:
            installed = CliAppManager(
                workspace=self.workspace, runtime=self.runtime
            ).installed_names()
        except Exception:
            installed = []
        installed_note = (
            f" Installed: {', '.join(installed)}."
            if installed
            else " No Settings CLI Apps installed."
        )
        return (
            "Run a user-installed CLI App (from Settings or @app). Unknown names are "
            "rejected; not for system CLIs (git, gh, python...). Uses argv, not shell." + installed_note
        )

    async def execute(
        self,
        name: str,
        args: list[str] | None = None,
        json: bool | None = False,
        working_dir: str | None = None,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> str:
        access = current_tool_workspace(
            self.workspace, restrict_to_workspace=self.restrict_to_workspace
        )
        workspace = access.project_path or self.workspace
        manager = CliAppManager(workspace=workspace, runtime=self.runtime)
        try:
            return manager.run(
                name,
                args=args or [],
                json_output=bool(json),
                working_dir=working_dir,
                timeout=timeout,
                restrict_to_workspace=access.restrict_to_workspace,
            )
        except CliAppError as exc:
            return f"Error: {exc.message}"
