from pathlib import Path

CONFIG_DIR_NAME = "vtx"


def get_config_dir() -> Path:
    return Path.home() / f".{CONFIG_DIR_NAME}"


def get_agents_dir() -> Path:
    return Path.home() / ".agents"
