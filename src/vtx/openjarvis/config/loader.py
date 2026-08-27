"""Config loader — VTX-native, inspired by openjarvis config.loader."""

from __future__ import annotations

from pathlib import Path

from vtx.core.paths import get_config_dir
from vtx.openjarvis.agent.config import OpenJarvisConfig
from vtx.openjarvis.config.schema import Config


def get_config_path() -> Path:
    return get_config_dir() / "openjarvis.json"


def load_config() -> Config:
    oj = OpenJarvisConfig.load()
    return Config.from_openjarvis(oj)
