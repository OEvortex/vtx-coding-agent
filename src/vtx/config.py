import contextlib
import os
import shutil
import sys
import tempfile
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, ValidationError, field_validator

from .themes import ColorsConfig, get_theme, get_theme_ids

CONFIG_DIR_NAME: str = "vtx"

OnOverflowMode = Literal["continue", "pause"]
AuthMode = Literal["auto", "required", "none"]
PermissionMode = Literal["prompt", "auto"]
NotificationMode = Literal["on", "off"]
PERMISSION_MODES: tuple[PermissionMode, ...] = get_args(PermissionMode)
NOTIFICATION_MODES: tuple[NotificationMode, ...] = get_args(NotificationMode)


# =================================================================================================
# Persisted Config Schema and Defaults
# =================================================================================================


def _load_default_config_yaml() -> dict[str, Any]:
    import yaml

    return (
        yaml.safe_load(
            resources.files("vtx.defaults").joinpath("config.yml").read_text(encoding="utf-8")
        )
        or {}
    )


_DEFAULT_CONFIG_DATA = _load_default_config_yaml()
CURRENT_CONFIG_VERSION = int(_DEFAULT_CONFIG_DATA.get("meta", {}).get("config_version", 1))


def _resolve_default_system_prompt() -> str:
    """Return the default base identity string.

    Pulled from :mod:`vtx.prompts.identity` so the prompt is owned by
    Python code rather than the shipped YAML. The YAML keeps an empty
    placeholder for schema stability; this function fills it in.
    """
    from .prompts.identity import DEFAULT_VTX_BASE

    return DEFAULT_VTX_BASE


_config_var: ContextVar["Config | None"] = ContextVar("vtx_config", default=None)
_config_warnings: list[str] = []


class MetaConfig(BaseModel):
    config_version: int = CURRENT_CONFIG_VERSION


ThinkingLinesOption = Literal["1", "2", "3", "4", "5", "none"]
THINKING_LINES_OPTIONS: tuple[ThinkingLinesOption, ...] = get_args(ThinkingLinesOption)


class UIConfig(BaseModel):
    theme: str = "gruvbox-dark"
    # When true, finalized thinking blocks are collapsed to a single line summary.
    # Set to false to always show the full thinking content.
    collapse_thinking: bool = True
    # Number of lines to show when thinking is collapsed. "none" means no truncation.
    thinking_lines: ThinkingLinesOption = "1"
    # When true, tool icon and name use badge label color on success.
    colored_tool_badge: bool = True
    # Show the list of keyboard shortcuts in the welcome section on launch.
    # Set to false to hide the shortcuts panel.
    show_welcome_shortcuts: bool = True
    # Models hidden from the /model picker. Use a provider name ("github-copilot")
    # to hide all its models, or "provider:model" to hide a specific model.
    # Hidden models remain usable via config defaults or session resume.
    hidden_models: list[str] = []
    # Provider slug whose models appear in the /model picker. Empty (default)
    # shows every provider; a non-empty slug restricts the picker to that one
    # provider. Pick via the /provider dropdown (or set directly).
    model_provider_filter: str = ""

    @field_validator("theme")
    @classmethod
    def _validate_theme(cls, value: str) -> str:
        if value not in get_theme_ids():
            raise ValueError(f"Unknown theme: {value}")
        return value

    @property
    def colors(self) -> ColorsConfig:
        return get_theme(self.theme).colors


class SystemPromptConfig(BaseModel):
    content: str
    git_context: bool = False


class AuthConfig(BaseModel):
    openai_compat: AuthMode = "auto"
    anthropic_compat: AuthMode = "auto"


class TLSConfig(BaseModel):
    insecure_skip_verify: bool = False


class LLMConfig(BaseModel):
    default_provider: str
    default_model: str
    default_base_url: str = ""
    default_thinking_level: str
    system_prompt: SystemPromptConfig
    tool_call_idle_timeout_seconds: float = 180
    request_timeout_seconds: float = 600
    auth: AuthConfig = AuthConfig()
    tls: TLSConfig = TLSConfig()


class CompactionConfig(BaseModel):
    on_overflow: OnOverflowMode = "continue"
    threshold_percent: float = 80.0


class GoalConfig(BaseModel):
    """Configuration for the ``/goal`` command.

    Goals let the agent keep working across turns until a separate
    evaluator judges a completion condition met. The settings below
    bound the run and pick the evaluator model.

    ``enabled`` is a master switch (matches Codex's
    ``features.goals = true``). When false the ``/goal`` command is
    rejected and the ``--goal`` CLI flag is ignored.

    ``max_turns`` is the per-goal turn cap. The loop ends with
    :class:`~vtx.events.GoalBudgetLimitedEvent` when this is hit. The
    YAML ``agent.max_turns`` knob is the global safety net.

    ``max_objective_chars`` is the cap on the user's goal text. Claude
    Code and Codex both use 4,000.

    ``evaluator_provider`` / ``evaluator_model``: empty string means
    "use the active default". Set them to route the evaluator through
    a cheaper / faster model when the plan supports it.
    """

    enabled: bool = True
    max_turns: int = 100
    max_objective_chars: int = 4000
    evaluator_provider: str = ""
    evaluator_model: str = ""


class AgentConfig(BaseModel):
    max_turns: int = 500
    default_context_window: int = 200000


class PermissionsConfig(BaseModel):
    mode: PermissionMode = "prompt"


class NotificationsConfig(BaseModel):
    enabled: bool = False
    volume: float = Field(default=0.5, ge=0.0, le=1.0)


class LastSelectedConfig(BaseModel):
    model_id: str | None = None
    provider: str | None = None
    thinking_level: str | None = None
    agent: str | None = None


class AgentsConfig(BaseModel):
    """Switchable handoff agents configuration.

    ``default`` is the name of the agent to activate at session start when
    no ``--agent`` flag is passed and ``VTX_AGENT`` is unset. Empty string
    (default) means "no agent active".

    ``switch_mode`` controls how Shift+Tab / ``/agent <name>`` behaves:

    * ``"lock"`` (default): the active agent is set at session start;
      switching via the TUI/CLI starts a new session JSONL preserving
      lineage. Cheap and predictable.
    * ``"hot"``: switching re-renders the system prompt + tools in place
      on the next turn. Experimental; the model may see a discontinuity.
    """

    default: str = ""
    switch_mode: str = "lock"  # "lock" | "hot"
    # Extra agent-file paths (project-local or global). Mirrors
    # ``extensions:`` for the extension system. Files in
    # ``<cwd>/.vtx/agent/`` and ``~/.vtx/agent/`` are always discovered.
    files: list[str] = Field(default_factory=list)


class SubagentPreset(BaseModel):
    """A built-in preset for the ``Task`` tool's ``subagent_type`` parameter.

    Mirrors the user-facing subset of :class:`~vtx.agents.AgentDef`: enough
    to constrain the sub-agent's tool surface, system-prompt instructions,
    and run budget without requiring a full ``.vtx/agent/<name>.py`` file.
    """

    name: str
    description: str
    instructions: str | None = None
    instructions_mode: str = "append"  # "append" | "replace"
    tools_allow: list[str] | None = None
    tools_deny: list[str] = Field(default_factory=list)
    model: str | None = None
    thinking_level: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    max_turns: int | None = Field(default=None, gt=0)


class TaskConfig(BaseModel):
    """Built-in sub-agent presets surfaced via the ``Task`` tool.

    The ``Task`` tool delegates work to a fresh sub-agent. ``subagent_type``
    is matched against, in order:

    1. A user-defined agent from ``.vtx/agent/<name>.py`` (the
       ``AgentRegistry``'s current contents).
    2. One of the named ``subagent_presets`` below.
    3. ``"general-purpose"`` as the final fallback.
    """

    subagent_presets: list[SubagentPreset] = Field(default_factory=list)


class ConfigSchema(BaseModel):
    meta: MetaConfig
    llm: LLMConfig
    ui: UIConfig
    compaction: CompactionConfig
    agent: AgentConfig
    permissions: PermissionsConfig
    notifications: NotificationsConfig = NotificationsConfig()
    goal: GoalConfig = GoalConfig()
    last_selected: LastSelectedConfig = LastSelectedConfig()
    # User-configured extension paths (file or package). Auto-discovered
    # directories (``.vtx/extensions/`` and ``~/.vtx/agent/extensions/``)
    # are always loaded in addition to this list unless ``--no-extensions``
    # is passed on the CLI.
    extensions: list[str] = Field(default_factory=list)
    # Switchable handoff agents (``.vtx/agent/<name>.py``).
    agents: AgentsConfig = AgentsConfig()
    # Built-in sub-agent presets for the ``Task`` tool.
    task: TaskConfig = TaskConfig()


# =================================================================================================
# Runtime Config Accessors
# =================================================================================================


class _BinariesConfig:
    def __init__(self, binaries: set[str]) -> None:
        self._binaries = binaries

    def has(self, binary: str) -> bool:
        return binary in self._binaries

    @property
    def rg(self) -> bool:
        return "rg" in self._binaries

    @property
    def fd(self) -> bool:
        return "fd" in self._binaries

    @property
    def gh(self) -> bool:
        return "gh" in self._binaries


class Config:
    def __init__(self, data: dict[str, Any]) -> None:
        merged = self.merge_with_defaults(data)
        self._parsed = ConfigSchema.model_validate(merged)

    @staticmethod
    def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(base)
        for key, value in overrides.items():
            current_value = merged.get(key)
            if isinstance(current_value, dict) and isinstance(value, dict):
                merged[key] = Config.deep_merge(current_value, value)
            else:
                merged[key] = deepcopy(value)
        return merged

    @staticmethod
    def _apply_legacy_key_shims(data: dict[str, Any]) -> dict[str, Any]:
        normalized_data = deepcopy(data)

        llm = normalized_data.get("llm")
        if isinstance(llm, dict):
            legacy_prompt = llm.get("system_prompt")
            if isinstance(legacy_prompt, str):
                llm["system_prompt"] = {"content": legacy_prompt}

            legacy_git_context = llm.pop("system_prompt_git_context", None)
            if isinstance(legacy_git_context, bool):
                system_prompt = llm.get("system_prompt")
                if not isinstance(system_prompt, dict):
                    system_prompt = {}
                    llm["system_prompt"] = system_prompt
                system_prompt.setdefault("git_context", legacy_git_context)

            # Fill the default base identity from Python when the YAML left
            # the placeholder empty (or did not include it at all).
            system_prompt = llm.get("system_prompt")
            if isinstance(system_prompt, dict) and not system_prompt.get("content"):
                system_prompt["content"] = _resolve_default_system_prompt()

        return normalized_data

    @staticmethod
    def merge_with_defaults(data: dict[str, Any]) -> dict[str, Any]:
        normalized_data = Config._apply_legacy_key_shims(data)
        return Config.deep_merge(_DEFAULT_CONFIG_DATA, normalized_data)

    @property
    def llm(self) -> LLMConfig:
        return self._parsed.llm

    @property
    def ui(self) -> UIConfig:
        return self._parsed.ui

    @property
    def compaction(self) -> CompactionConfig:
        return self._parsed.compaction

    @property
    def goal(self) -> GoalConfig:
        return self._parsed.goal

    @property
    def agent(self) -> AgentConfig:
        return self._parsed.agent

    @property
    def permissions(self) -> PermissionsConfig:
        return self._parsed.permissions

    @property
    def notifications(self) -> NotificationsConfig:
        return self._parsed.notifications

    @property
    def binaries(self) -> _BinariesConfig:
        return _BinariesConfig(AVAILABLE_BINARIES)

    @property
    def extensions(self) -> list[str]:
        return self._parsed.extensions

    @property
    def agents(self) -> AgentsConfig:
        return self._parsed.agents

    @property
    def task(self) -> TaskConfig:
        return self._parsed.task


# =================================================================================================
# Persisted Config IO, Migration, and Serialization
# =================================================================================================


def get_config_dir() -> Path:
    return Path.home() / f".{CONFIG_DIR_NAME}"


def get_agents_dir() -> Path:
    return Path.home() / ".agents"


def _ensure_config_file() -> Path:
    config_dir = get_config_dir()
    config_file = config_dir / "config.yml"

    if not config_file.exists():
        config_dir.mkdir(parents=True, exist_ok=True)
        import yaml

        defaults = Config._apply_legacy_key_shims(_load_default_config_yaml())
        config_file.write_text(yaml.dump(defaults, default_flow_style=False), encoding="utf-8")

    return config_file


def _record_config_warning(message: str) -> None:
    _config_warnings.append(message)
    print(message, file=sys.stderr)


def consume_config_warnings() -> list[str]:
    warnings = _config_warnings.copy()
    _config_warnings.clear()
    return warnings


def _detect_available_binaries() -> set[str]:
    binaries = {"rg", "fd", "gh"}
    available = set()
    bin_dir = get_config_dir() / "bin"

    for binary in binaries:
        if shutil.which(binary) or (bin_dir / binary).exists():
            available.add(binary)

    return available


def _get_config_version(data: dict[str, Any]) -> int:
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return 0
    version = meta.get("config_version")
    if isinstance(version, int) and version >= 0:
        return version
    return 0


def _migrate_v0_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    migrated = Config._apply_legacy_key_shims(data)
    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 1}
    else:
        meta["config_version"] = 1
    return migrated


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    migrated = Config._apply_legacy_key_shims(data)
    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 2}
    else:
        meta["config_version"] = 2
    return migrated


def _migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    migrated = Config._apply_legacy_key_shims(data)
    ui = migrated.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        migrated["ui"] = ui

    ui["theme"] = "gruvbox-dark"
    ui.pop("colors", None)

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 3}
    else:
        meta["config_version"] = 3
    return migrated


def _migrate_v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    migrated = Config._apply_legacy_key_shims(data)
    llm = migrated.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        migrated["llm"] = llm

    auth = llm.get("auth")
    if not isinstance(auth, dict):
        auth = {}
        llm["auth"] = auth

    auth.setdefault("openai_compat", "auto")
    auth.setdefault("anthropic_compat", "auto")

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 4}
    else:
        meta["config_version"] = 4
    return migrated


def _migrate_v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    migrated = Config._apply_legacy_key_shims(data)
    notifications = migrated.get("notifications")
    if not isinstance(notifications, dict):
        notifications = {}
        migrated["notifications"] = notifications

    notifications.setdefault("volume", 0.5)

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 5}
    else:
        meta["config_version"] = 5
    return migrated


def _migrate_v5_to_v6(data: dict[str, Any]) -> dict[str, Any]:
    migrated = Config._apply_legacy_key_shims(data)
    llm = migrated.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        migrated["llm"] = llm

    system_prompt = llm.get("system_prompt")
    if not isinstance(system_prompt, dict):
        system_prompt = {}
        llm["system_prompt"] = system_prompt

    # Pull the default identity from Python so the YAML placeholder
    # remains a single source of truth.
    system_prompt["content"] = _resolve_default_system_prompt()
    system_prompt["git_context"] = _DEFAULT_CONFIG_DATA["llm"]["system_prompt"]["git_context"]

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 6}
    else:
        meta["config_version"] = 6
    return migrated


def _migrate_v6_to_v7(data: dict[str, Any]) -> dict[str, Any]:
    """Add the ``extensions:`` list. Pre-v7 users get an empty default.

    The first attempt at this migration used ``{paths: [...]}`` (a dict).
    We now use a flat list, so we coerce the old shape to its ``paths`` value
    if present.
    """
    migrated = Config._apply_legacy_key_shims(data)
    extensions = migrated.get("extensions")
    if isinstance(extensions, dict):
        # Legacy v7-with-dict shape: pull out the list.
        if isinstance(extensions.get("paths"), list):
            migrated["extensions"] = list(extensions["paths"])
        else:
            migrated["extensions"] = []
    elif not isinstance(extensions, list):
        migrated["extensions"] = []

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 7}
    else:
        meta["config_version"] = 7
    return migrated


def _migrate_v7_to_v8(data: dict[str, Any]) -> dict[str, Any]:
    """Add the ``ui.model_provider_filter`` field. Pre-v8 users get an empty string."""
    migrated = Config._apply_legacy_key_shims(data)
    ui = migrated.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        migrated["ui"] = ui

    if not isinstance(ui.get("model_provider_filter"), str):
        ui["model_provider_filter"] = ""

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 8}
    else:
        meta["config_version"] = 8
    return migrated


def _migrate_v8_to_v9(data: dict[str, Any]) -> dict[str, Any]:
    """Add the ``agents:`` block and ``last_selected.agent``.

    Pre-v9 users get an empty default (no agent active at session start).
    """
    migrated = Config._apply_legacy_key_shims(data)

    # Top-level agents block.
    agents = migrated.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        migrated["agents"] = agents
    agents.setdefault("default", "")
    agents.setdefault("switch_mode", "lock")
    if not isinstance(agents.get("files"), list):
        agents["files"] = []

    # last_selected.agent (per-session agent name).
    last_selected = migrated.get("last_selected")
    if not isinstance(last_selected, dict):
        last_selected = {}
        migrated["last_selected"] = last_selected
    if "agent" not in last_selected:
        last_selected["agent"] = None

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 9}
    else:
        meta["config_version"] = 9
    return migrated


def _migrate_v9_to_v10(data: dict[str, Any]) -> dict[str, Any]:
    """Add the ``task.subagent_presets`` block. Pre-v10 users get the
    built-in defaults (general-purpose / Explore / Plan).
    """
    migrated = Config._apply_legacy_key_shims(data)

    task = migrated.get("task")
    if not isinstance(task, dict):
        task = {}
        migrated["task"] = task
    if not isinstance(task.get("subagent_presets"), list):
        task["subagent_presets"] = list(_DEFAULT_CONFIG_DATA["task"]["subagent_presets"])

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 10}
    else:
        meta["config_version"] = 10
    return migrated


def _migrate_v10_to_v11(data: dict[str, Any]) -> dict[str, Any]:
    """Add the ``goal:`` block. Pre-v11 users get the built-in defaults.

    Matches the addition of the ``/goal`` command: an opt-in master
    switch, a turn cap, the 4,000-char objective limit from Claude
    Code / Codex, and empty evaluator-model overrides (which fall
    back to the active default).
    """
    migrated = Config._apply_legacy_key_shims(data)

    goal = migrated.get("goal")
    if not isinstance(goal, dict):
        goal = {}
        migrated["goal"] = goal

    goal.setdefault("enabled", _DEFAULT_CONFIG_DATA["goal"]["enabled"])
    goal.setdefault("max_turns", _DEFAULT_CONFIG_DATA["goal"]["max_turns"])
    goal.setdefault("max_objective_chars", _DEFAULT_CONFIG_DATA["goal"]["max_objective_chars"])
    goal.setdefault("evaluator_provider", _DEFAULT_CONFIG_DATA["goal"]["evaluator_provider"])
    goal.setdefault("evaluator_model", _DEFAULT_CONFIG_DATA["goal"]["evaluator_model"])

    meta = migrated.get("meta")
    if not isinstance(meta, dict):
        migrated["meta"] = {"config_version": 11}
    else:
        meta["config_version"] = 11
    return migrated


def _migrate_config_data(data: dict[str, Any]) -> tuple[dict[str, Any], int, int, bool]:
    original = deepcopy(data)
    current_version = _get_config_version(original)
    migrated = deepcopy(original)

    while current_version < CURRENT_CONFIG_VERSION:
        if current_version == 0:
            migrated = _migrate_v0_to_v1(migrated)
            current_version = 1
            continue
        if current_version == 1:
            migrated = _migrate_v1_to_v2(migrated)
            current_version = 2
            continue
        if current_version == 2:
            migrated = _migrate_v2_to_v3(migrated)
            current_version = 3
            continue
        if current_version == 3:
            migrated = _migrate_v3_to_v4(migrated)
            current_version = 4
            continue
        if current_version == 4:
            migrated = _migrate_v4_to_v5(migrated)
            current_version = 5
            continue
        if current_version == 5:
            migrated = _migrate_v5_to_v6(migrated)
            current_version = 6
            continue
        if current_version == 6:
            migrated = _migrate_v6_to_v7(migrated)
            current_version = 7
            continue
        if current_version == 7:
            migrated = _migrate_v7_to_v8(migrated)
            current_version = 8
            continue
        if current_version == 8:
            migrated = _migrate_v8_to_v9(migrated)
            current_version = 9
            continue
        if current_version == 9:
            migrated = _migrate_v9_to_v10(migrated)
            current_version = 10
            continue
        if current_version == 10:
            migrated = _migrate_v10_to_v11(migrated)
            current_version = 11
            continue
        break

    migrated_version = _get_config_version(migrated)
    did_migrate = migrated != original
    return migrated, _get_config_version(original), migrated_version, did_migrate


def _serialize_config_yaml(data: dict[str, Any]) -> str:
    import yaml

    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def _backup_and_write_migrated_config(config_file: Path, data: dict[str, Any]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = config_file.with_name(f"{config_file.name}.bak.{timestamp}")
    shutil.copy2(config_file, backup_path)
    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return backup_path


# =================================================================================================
# Runtime Environment Capabilities
# TODO: Consider moving runtime capability detection and caching to a dedicated runtime.py module.
# =================================================================================================


AVAILABLE_BINARIES = _detect_available_binaries()


def update_available_binaries() -> None:
    AVAILABLE_BINARIES.clear()
    AVAILABLE_BINARIES.update(_detect_available_binaries())


# =================================================================================================
# Persisted Config Loading and Runtime Cache
# =================================================================================================


def _read_config_data(config_file: Path) -> dict[str, Any]:
    try:
        import yaml

        with open(config_file, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        _record_config_warning(
            f"Invalid config at {config_file}: {exc}. Falling back to built-in defaults."
        )
        return {}


def _load_config() -> Config:
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    try:
        migrated_data, from_version, to_version, did_migrate = _migrate_config_data(data)
        if did_migrate and data:
            try:
                backup = _backup_and_write_migrated_config(config_file, migrated_data)
                _record_config_warning(
                    f"Migrated config at {config_file} from v{from_version} to v{to_version}. "
                    f"Backup saved to {backup}."
                )
            except Exception as exc:
                _record_config_warning(
                    f"Failed to persist migrated config at {config_file}: {exc}. "
                    "Continuing with in-memory migrated config."
                )
        return Config(migrated_data)
    except ValidationError as exc:
        _record_config_warning(
            f"Invalid config values at {config_file}: {exc}. Falling back to built-in defaults."
        )
        return Config({})


def get_config() -> Config:
    """
    Get the current config instance.

    Returns the config from context variable if set, otherwise loads from file.
    The loaded config is cached in the context variable.
    """
    cfg = _config_var.get()
    if cfg is None:
        cfg = _load_config()
        _config_var.set(cfg)
    return cfg


def set_config(config: Config) -> None:
    """Set the config instance (useful for testing)."""
    _config_var.set(config)


def reload_config() -> Config:
    """Reload config from file and update the context variable."""
    cfg = _load_config()
    _config_var.set(cfg)
    return cfg


def _set_config_version(data: dict[str, Any]) -> None:
    meta = data.get("meta")
    if not isinstance(meta, dict):
        data["meta"] = {"config_version": CURRENT_CONFIG_VERSION}
    else:
        meta["config_version"] = CURRENT_CONFIG_VERSION


def set_theme(theme: str) -> Config:
    get_theme(theme)

    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        data["ui"] = ui

    ui["theme"] = theme
    ui.pop("colors", None)
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def set_show_welcome_shortcuts(enabled: bool) -> Config:
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        data["ui"] = ui

    ui["show_welcome_shortcuts"] = enabled
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def set_permissions_mode(mode: PermissionMode) -> Config:
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    perms = data.get("permissions")
    if not isinstance(perms, dict):
        perms = {}
        data["permissions"] = perms

    perms["mode"] = mode
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def set_thinking_lines(lines: ThinkingLinesOption) -> Config:
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        data["ui"] = ui

    ui["thinking_lines"] = lines
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def set_git_context(enabled: bool) -> Config:
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    llm = data.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        data["llm"] = llm

    system_prompt = llm.get("system_prompt")
    if not isinstance(system_prompt, dict):
        system_prompt = {}
        llm["system_prompt"] = system_prompt

    system_prompt["git_context"] = enabled
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def set_colored_tool_badge(enabled: bool) -> Config:
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        data["ui"] = ui

    ui["colored_tool_badge"] = enabled
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def set_notifications_enabled(enabled: bool) -> Config:
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    notifications = data.get("notifications")
    if not isinstance(notifications, dict):
        notifications = {}
        data["notifications"] = notifications

    notifications["enabled"] = enabled
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def set_model_provider_filter(provider: str) -> Config:
    """Set the single provider slug shown in the /model picker.

    Pass ``""`` to clear the filter (show every provider). Unknown slugs
    are dropped so a typo never persists.
    """
    from .llm.provider_catalog import _load as _load_providers

    cleaned = provider.strip()
    if cleaned and cleaned not in _load_providers():
        cleaned = ""

    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    ui = data.get("ui")
    if not isinstance(ui, dict):
        ui = {}
        data["ui"] = ui

    ui["model_provider_filter"] = cleaned
    _set_config_version(data)

    _atomic_write_text(config_file, _serialize_config_yaml(data))
    return reload_config()


def reset_config() -> None:
    """Reset config to uninitialized state (next get_config() will reload from file)."""
    _config_var.set(None)
    _config_warnings.clear()


def set_last_selected(
    model_id: str | None,
    provider: str | None,
    thinking_level: str | None,
    agent: str | None = None,
) -> None:
    """Save the last selected model, provider, thinking level, and agent."""
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    last_selected = data.get("last_selected", {})
    if not isinstance(last_selected, dict):
        last_selected = {}

    last_selected["model_id"] = model_id
    last_selected["provider"] = provider
    last_selected["thinking_level"] = thinking_level
    last_selected["agent"] = agent

    data["last_selected"] = last_selected
    _set_config_version(data)
    _atomic_write_text(config_file, _serialize_config_yaml(data))


def get_last_selected() -> LastSelectedConfig:
    """Get the last selected model, provider, thinking level, and agent."""
    config_file = _ensure_config_file()
    data = _read_config_data(config_file)

    last_selected = data.get("last_selected", {})
    if not isinstance(last_selected, dict):
        last_selected = {}

    return LastSelectedConfig(
        model_id=last_selected.get("model_id"),
        provider=last_selected.get("provider"),
        thinking_level=last_selected.get("thinking_level"),
        agent=last_selected.get("agent"),
    )
