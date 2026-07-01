"""Configuration schema using Pydantic."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import AliasChoices, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from vtx_claw.config_base import Base
from vtx_claw.cron.types import CronSchedule

if TYPE_CHECKING:
    from vtx_claw.agent.tools.cli_apps import CliAppsToolConfig
    from vtx_claw.agent.tools.filesystem import FileToolsConfig
    from vtx_claw.agent.tools.image_generation import ImageGenerationToolConfig
    from vtx_claw.agent.tools.self import MyToolConfig
    from vtx_claw.agent.tools.shell import ExecToolConfig
    from vtx_claw.agent.tools.web import WebToolsConfig


class ChannelsConfig(Base):
    """Configuration for chat channels.

    Built-in and plugin channel configs are stored as extra fields (dicts).
    Each channel parses its own config in __init__.
    Per-channel "streaming": true enables streaming output (requires send_delta impl).
    """

    model_config = ConfigDict(extra="allow")

    send_progress: bool = True  # stream agent's text progress to the channel
    send_tool_hints: bool = False  # stream tool-call hints (e.g. read_file("…"))
    show_reasoning: bool = True  # surface model reasoning when channel implements it
    extract_document_text: bool = (
        True  # extract text from document attachments before sending to the model
    )
    send_max_retries: int = Field(
        default=3, ge=0, le=10
    )  # Max delivery attempts (initial send included)
    transcription_provider: str = "groq"  # Deprecated: use top-level transcription.provider
    transcription_language: str | None = Field(
        default=None, pattern=r"^[a-z]{2,3}$"
    )  # Deprecated: use top-level transcription.language


class TranscriptionConfig(Base):
    """Cross-channel audio transcription configuration."""

    enabled: bool = True
    provider: str | None = None  # Validated by vtx_claw.audio.transcription_registry.
    model: str | None = None
    language: str | None = Field(default=None, pattern=r"^[a-z]{2,3}$")
    max_duration_sec: int = Field(default=120, ge=1, le=600)
    max_upload_mb: int = Field(default=25, ge=1, le=100)


class DreamConfig(Base):
    """Dream memory consolidation configuration."""

    _HOUR_MS = 3_600_000

    enabled: bool = True  # Register the periodic Dream consolidation job on startup
    interval_h: int = Field(default=2, ge=1)  # Every 2 hours by default
    cron: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )  # Legacy cron expression override
    model_override: str | None = Field(
        default=None, validation_alias=AliasChoices("modelOverride", "model", "model_override")
    )  # Override model for Dream sessions (pending implementation)
    max_batch_size: int = Field(default=20, ge=1)  # Deprecated: no longer used
    max_iterations: int = Field(default=15, ge=1)  # Deprecated: no longer used
    annotate_line_ages: bool = True  # Deprecated: no longer used

    def build_schedule(self, timezone: str) -> CronSchedule:
        """Build the runtime schedule, preferring the legacy cron override if present."""
        if self.cron:
            return CronSchedule(kind="cron", expr=self.cron, tz=timezone)
        return CronSchedule(kind="every", every_ms=self.interval_h * self._HOUR_MS)

    def describe_schedule(self) -> str:
        """Return a human-readable summary for logs and startup output."""
        if self.cron:
            return f"cron {self.cron} (legacy)"
        hours = self.interval_h
        return f"every {hours}h"


class InlineFallbackConfig(Base):
    """One inline fallback model configuration."""

    model: str
    provider: str
    max_tokens: int | None = None
    context_window_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None


FallbackCandidate = str | InlineFallbackConfig


class ModelPresetConfig(Base):
    """A named set of model + generation parameters for quick switching."""

    label: str | None = None
    model: str
    provider: str = "auto"
    max_tokens: int = 8192
    context_window_tokens: int = 200_000
    temperature: float = 0.1
    reasoning_effort: str | None = None

    def to_generation_settings(self) -> Any:
        from vtx_claw.providers.base import GenerationSettings

        return GenerationSettings(
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=self.reasoning_effort,
        )


class AgentDefaults(Base):
    """Default agent configuration."""

    workspace: str = "~/.vtx/claw/workspace"
    model_preset: str | None = None  # Active preset name — takes precedence over fields below
    model: str = "anthropic/claude-opus-4-5"
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    )
    max_tokens: int = 8192
    context_window_tokens: int = 200_000
    context_block_limit: int | None = None
    temperature: float = 0.1
    fallback_models: list[FallbackCandidate] = Field(default_factory=list)
    max_tool_iterations: int = 200
    max_concurrent_subagents: int = Field(default=1, ge=1)
    fail_on_tool_error: bool = True
    max_tool_result_chars: int = 16_000
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    tool_hint_max_length: int = Field(
        default=40,
        ge=20,
        le=500,
        validation_alias=AliasChoices("toolHintMaxLength"),
        serialization_alias="toolHintMaxLength",
    )  # Max characters for tool hint display (e.g. "$ cd …/project && npm test")
    reasoning_effort: str | None = (
        None  # low / medium / high / adaptive / none — LLM thinking effort; None preserves the provider default
    )
    timezone: str = "UTC"  # IANA timezone, e.g. "Asia/Shanghai", "America/New_York"
    bot_name: str = "vtx_claw"  # Display name shown in CLI prompts (e.g. "{name} is thinking...")
    bot_icon: str = (
        "🐈"  # Short icon (emoji or text) shown next to the bot name in CLI; "" to omit
    )
    unified_session: bool = (
        False  # Share one session across all channels (single-user multi-device)
    )
    disabled_skills: list[str] = Field(
        default_factory=list
    )  # Skill names to exclude from loading (e.g. ["summarize", "skill-creator"])
    session_ttl_minutes: int = Field(
        default=15,
        ge=0,
        validation_alias=AliasChoices("idleCompactAfterMinutes", "sessionTtlMinutes"),
        serialization_alias="idleCompactAfterMinutes",
    )  # Auto-compact idle threshold in minutes (0 = disabled)
    consolidation_ratio: float = Field(
        default=0.5,
        ge=0.1,
        le=0.95,
        validation_alias=AliasChoices("consolidationRatio"),
        serialization_alias="consolidationRatio",
    )  # Consolidation target ratio (0.5 = 50% of budget retained after compression)
    dream: DreamConfig = Field(default_factory=DreamConfig)


class AgentsConfig(Base):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider configuration."""

    api_key: str | None = Field(default=None, repr=False)
    api_base: str | None = None
    api_type: Literal["auto", "chat_completions", "responses"] = "auto"  # Request API surface
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)
    extra_body: dict[str, Any] | None = (
        None  # Extra provider request fields; shape depends on provider/API surface
    )
    extra_query: dict[str, str] | None = (
        None  # Extra query params (e.g. api-version for Azure-style gateways)
    )
    thinking_style: str | None = None  # Thinking/reasoning style for custom providers

    # Valid values mirror the keys of _THINKING_STYLE_MAP in
    # vtx_claw/providers/openai_compat_provider.py. Kept duplicated here to
    # avoid an import cycle (schema.py must not import from providers/).
    _VALID_THINKING_STYLES: ClassVar[tuple[str, ...]] = (
        "thinking_type",
        "enable_thinking",
        "reasoning_split",
    )

    @field_validator("thinking_style")
    @classmethod
    def _validate_thinking_style(cls, v: str | None) -> str | None:
        if not v:  # None or "" -> no injection, valid (backwards compatible)
            return v
        if v not in cls._VALID_THINKING_STYLES:
            raise ValueError(
                f"Invalid thinking_style {v!r}. "
                f"Must be one of: {', '.join(repr(s) for s in cls._VALID_THINKING_STYLES)} "
                f"(or empty/omitted)."
            )
        return v


class BedrockProviderConfig(ProviderConfig):
    """AWS Bedrock Runtime provider configuration."""

    region: str | None = None  # AWS region, falls back to AWS_REGION/AWS_DEFAULT_REGION/profile
    profile: str | None = None  # Optional AWS shared config profile


class ProvidersConfig(Base):
    """Configuration for LLM providers.

    Providers are resolved at runtime from vtx's provider catalog via the
    vtx bridge (``merge_vtx_config``).  No hardcoded provider fields here
    — any additional field becomes an OpenAI-compatible custom provider.
    """

    model_config = ConfigDict(extra="allow")

    custom: ProviderConfig = Field(
        default_factory=ProviderConfig
    )  # Any OpenAI-compatible endpoint

    @model_validator(mode="after")
    def convert_extra_providers(self):
        """Convert extra fields (custom providers) to ProviderConfig objects."""
        if self.model_extra:
            from vtx_claw.providers.registry import find_by_name

            for key, value in self.model_extra.items():
                if spec := find_by_name(key):
                    raise ValueError(
                        f"providers.{key} conflicts with built-in provider {spec.name!r}; "
                        "use the built-in provider key or choose a different custom provider name"
                    )
                if isinstance(value, dict):
                    self.model_extra[key] = ProviderConfig.model_validate(value)
        return self

    @model_validator(mode="after")
    def _validate_api_type_scope(self) -> ProvidersConfig:
        for name in self.__class__.model_fields:
            if name == "openai":
                continue
            provider = getattr(self, name, None)
            if isinstance(provider, ProviderConfig) and provider.api_type != "auto":
                raise ValueError(
                    "providers.<name>.api_type is only supported for providers.openai"
                )
        for provider in (self.model_extra or {}).values():
            if isinstance(provider, ProviderConfig) and provider.api_type != "auto":
                raise ValueError(
                    "providers.<name>.api_type is only supported for providers.openai"
                )
        return self


class HeartbeatConfig(Base):
    """Heartbeat service configuration (now backed by cron)."""

    enabled: bool = True
    interval_s: int = 30 * 60  # 30 minutes
    keep_recent_messages: int = 8


class ApiConfig(Base):
    """OpenAI-compatible API server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 8900
    timeout: float = 120.0  # Per-request timeout in seconds.


class GatewayConfig(Base):
    """Gateway/server configuration."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class MCPServerConfig(Base):
    """MCP server connection configuration (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # auto-detected if omitted
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars
    cwd: str = ""  # Stdio: working directory for MCP server runtime artifacts
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers
    tool_timeout: int = 30  # seconds before a tool call is cancelled
    enabled_tools: list[str] = Field(
        default_factory=lambda: ["*"]
    )  # Only register these tools; accepts raw MCP names or wrapped mcp_<server>_<tool> names; ["*"] = all capabilities (tools, resources, prompts); any restriction = only listed tools, no resources/prompts


def _lazy_default(module_path: str, class_name: str) -> Any:
    """Deferred import helper for ToolsConfig default factories."""
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


class ToolsConfig(Base):
    """Tools configuration.

    Field types for tool-specific sub-configs are resolved via model_rebuild()
    at the bottom of this file so tool config classes can stay next to their
    tool implementations.
    """

    web: WebToolsConfig = Field(
        default_factory=lambda: _lazy_default("vtx_claw.agent.tools.web", "WebToolsConfig")
    )
    exec: ExecToolConfig = Field(
        default_factory=lambda: _lazy_default("vtx_claw.agent.tools.shell", "ExecToolConfig")
    )
    file: FileToolsConfig = Field(
        default_factory=lambda: _lazy_default("vtx_claw.agent.tools.filesystem", "FileToolsConfig")
    )
    cli_apps: CliAppsToolConfig = Field(
        default_factory=lambda: _lazy_default("vtx_claw.agent.tools.cli_apps", "CliAppsToolConfig")
    )
    my: MyToolConfig = Field(
        default_factory=lambda: _lazy_default("vtx_claw.agent.tools.self", "MyToolConfig")
    )
    image_generation: ImageGenerationToolConfig = Field(
        default_factory=lambda: _lazy_default(
            "vtx_claw.agent.tools.image_generation", "ImageGenerationToolConfig"
        )
    )
    restrict_to_workspace: bool = (
        False  # policy intent: keep tool access inside workspace when possible
    )
    webui_allow_local_service_access: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "webuiAllowLocalServiceAccess",
            "webui_allow_local_service_access",
            "allowLocalPreviewAccess",
            "allow_local_preview_access",
        ),
    )  # allow WebUI Full Access shell checks against localhost services; legacy allowLocalPreviewAccess still reads
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    ssrf_whitelist: list[str] = Field(
        default_factory=list
    )  # CIDR ranges to exempt from SSRF blocking (e.g. ["100.64.0.0/10"] for Tailscale)


class Config(BaseSettings):
    """Root configuration for vtx_claw."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    transcription: TranscriptionConfig = Field(default_factory=TranscriptionConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    model_presets: dict[str, ModelPresetConfig] = Field(
        default_factory=dict, validation_alias=AliasChoices("modelPresets", "model_presets")
    )

    def __init__(self, **values: Any) -> None:
        if not type(self).__pydantic_complete__:
            _resolve_tool_config_refs()
        super().__init__(**values)

    @model_validator(mode="after")
    def _validate_model_preset(self) -> Config:
        if "default" in self.model_presets:
            raise ValueError("model_preset name 'default' is reserved for agents.defaults")
        name = self.agents.defaults.model_preset
        if name and name != "default" and name not in self.model_presets:
            raise ValueError(f"model_preset {name!r} not found in model_presets")
        for fallback in self.agents.defaults.fallback_models:
            if isinstance(fallback, str) and fallback not in self.model_presets:
                raise ValueError(f"fallback_models entry {fallback!r} not found in model_presets")
        return self

    def resolve_default_preset(self) -> ModelPresetConfig:
        """Return the implicit `default` preset from agents.defaults fields."""
        d = self.agents.defaults
        return ModelPresetConfig(
            model=d.model,
            provider=d.provider,
            max_tokens=d.max_tokens,
            context_window_tokens=d.context_window_tokens,
            temperature=d.temperature,
            reasoning_effort=d.reasoning_effort,
        )

    def resolve_preset(self, name: str | None = None) -> ModelPresetConfig:
        """Return effective model params from a named preset or the implicit default."""
        name = self.agents.defaults.model_preset if name is None else name
        if not name or name == "default":
            return self.resolve_default_preset()
        if name not in self.model_presets:
            raise KeyError(f"model_preset {name!r} not found in model_presets")
        return self.model_presets[name]

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None, *, preset: ModelPresetConfig | None = None
    ) -> tuple[ProviderConfig | None, str | None]:
        """Match provider config and its registry name. Returns (config, spec_name).

        Provider metadata comes from vtx's provider catalog (not a local
        ``PROVIDERS`` list).  The vtx bridge sets ``agents.defaults.provider``
        to the exact vtx slug, so the forced-provider path (step 1) is the
        primary resolution path.
        """
        from vtx_claw.providers.registry import find_by_name

        resolved = preset or self.resolve_preset()
        forced = resolved.provider

        def _custom_provider_by_name(name: str) -> tuple[ProviderConfig, str] | None:
            normalized = name.replace("-", "_").lower()
            for attr_name, provider in (self.providers.model_extra or {}).items():
                if not isinstance(provider, ProviderConfig):
                    continue
                if attr_name.replace("-", "_").lower() == normalized:
                    return provider, attr_name
            return None

        def _get_provider_config(name: str) -> ProviderConfig | None:
            """Get a ProviderConfig from either a named field or model_extra."""
            p = getattr(self.providers, name, None)
            if p is not None:
                return p
            extra = (self.providers.model_extra or {}).get(name)
            return extra if isinstance(extra, ProviderConfig) else None

        if forced != "auto":
            spec = find_by_name(forced)
            if spec:
                p = _get_provider_config(spec.name)
                return (p, spec.name) if p else (None, None)
            custom = _custom_provider_by_name(forced)
            if custom is not None:
                return custom
            return None, None

        # When provider is "auto", match by model name prefix from vtx catalog.
        model_lower = (model or resolved.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        # Check model_extra (custom) providers by prefix first
        if model_prefix:
            custom = _custom_provider_by_name(normalized_prefix)
            if custom is not None:
                return custom

        # Iterate vtx catalog for prefix/keyword matching
        from vtx_claw.providers.registry import list_providers

        for spec in list_providers():
            p = _get_provider_config(spec.name)
            if not p:
                continue
            # Prefix match
            if model_prefix and normalized_prefix == spec.name:
                return p, spec.name
            # Keyword match
            if any(
                kw in model_lower or kw.replace("-", "_") in model_normalized
                for kw in spec.keywords
            ) and (spec.is_oauth or spec.is_local or spec.is_direct or p.api_key):
                return p, spec.name

        # Fallback: any configured provider with an API key
        for spec in list_providers():
            p = _get_provider_config(spec.name)
            if p and p.api_key:
                return p, spec.name

        # Final fallback: custom providers with api_base
        for attr_name, p in (self.providers.model_extra or {}).items():
            if isinstance(p, ProviderConfig) and p.api_base:
                return p, attr_name

        return None, None

    def get_provider(
        self, model: str | None = None, *, preset: ModelPresetConfig | None = None
    ) -> ProviderConfig | None:
        """Get matched provider config (api_key, api_base, extra_headers). Falls back to first available."""
        p, _ = self._match_provider(model, preset=preset)
        return p

    def get_provider_name(
        self, model: str | None = None, *, preset: ModelPresetConfig | None = None
    ) -> str | None:
        """Get the registry name of the matched provider (e.g. "deepseek", "openrouter")."""
        _, name = self._match_provider(model, preset=preset)
        return name

    def get_api_key(
        self, model: str | None = None, *, preset: ModelPresetConfig | None = None
    ) -> str | None:
        """Get API key for the given model. Falls back to first available key."""
        p = self.get_provider(model, preset=preset)
        return p.api_key if p else None

    def get_api_base(
        self, model: str | None = None, *, preset: ModelPresetConfig | None = None
    ) -> str | None:
        """Get API base URL for the given model, falling back to the provider default when present."""
        from vtx_claw.providers.registry import find_by_name

        p, name = self._match_provider(model, preset=preset)
        if p and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="VTX_CLAW_", env_nested_delimiter="__")  # type: ignore[call-arg]


def _resolve_tool_config_refs() -> None:
    """Resolve forward references in ToolsConfig by importing tool config classes.

    Must be called after all modules are loaded (breaks circular imports).
    Re-exports the classes into this module's namespace so existing imports
    like ``from vtx_claw.config.schema import ExecToolConfig`` continue to work.
    """
    import sys

    from vtx_claw.agent.tools.cli_apps import CliAppsToolConfig
    from vtx_claw.agent.tools.filesystem import FileToolsConfig
    from vtx_claw.agent.tools.image_generation import ImageGenerationToolConfig
    from vtx_claw.agent.tools.self import MyToolConfig
    from vtx_claw.agent.tools.shell import ExecToolConfig
    from vtx_claw.agent.tools.web import WebFetchConfig, WebSearchConfig, WebToolsConfig

    # Re-export into this module's namespace
    mod = sys.modules[__name__]
    mod.ExecToolConfig = ExecToolConfig  # type: ignore[unresolved-attribute]
    mod.FileToolsConfig = FileToolsConfig  # type: ignore[unresolved-attribute]
    mod.CliAppsToolConfig = CliAppsToolConfig  # type: ignore[unresolved-attribute]
    mod.WebToolsConfig = WebToolsConfig  # type: ignore[unresolved-attribute]
    mod.WebSearchConfig = WebSearchConfig  # type: ignore[unresolved-attribute]
    mod.WebFetchConfig = WebFetchConfig  # type: ignore[unresolved-attribute]
    mod.MyToolConfig = MyToolConfig  # type: ignore[unresolved-attribute]
    mod.ImageGenerationToolConfig = ImageGenerationToolConfig  # type: ignore[unresolved-attribute]

    ToolsConfig.model_rebuild()
    Config.model_rebuild()


# Eagerly resolve when the import chain allows it (no circular deps at this
# point).  If it fails (first import triggers a cycle), the rebuild will
# happen lazily when Config/ToolsConfig is first used at runtime.
with contextlib.suppress(ImportError):
    _resolve_tool_config_refs()
