from __future__ import annotations

import logging
from typing import Any

from vtx_claw.config.schema import SecurityConfig

logger = logging.getLogger(__name__)

PRESETS: dict[str, dict[str, Any]] = {
    "relaxed": {
        "default_policy": "open",
        "default_preset": "relaxed",
        "exec_policy": "full",
        "safe_bins": [],
        "exec_allowlist": [],
    },
    "trusted": {
        "default_policy": "allowlist",
        "default_preset": "trusted",
        "exec_policy": "allowlist",
        "safe_bins": ["python", "git", "npm", "node", "pip", "uv"],
        "exec_allowlist": ["*"],
    },
    "standard": {
        "default_policy": "pairing",
        "default_preset": "standard",
        "exec_policy": "on-miss",
        "safe_bins": ["python", "git"],
        "exec_allowlist": [],
    },
    "strict": {
        "default_policy": "disabled",
        "default_preset": "strict",
        "exec_policy": "deny",
        "safe_bins": [],
        "exec_allowlist": [],
    },
}


def apply_preset(cfg: SecurityConfig, preset: str) -> SecurityConfig:
    if preset not in PRESETS:
        raise ValueError(f"Unknown preset: {preset}")
    p = PRESETS[preset]
    cfg.default_policy = p["default_policy"]
    cfg.default_preset = p["default_preset"]
    cfg.exec_policy = p["exec_policy"]
    cfg.safe_bins = list(p["safe_bins"])
    cfg.exec_allowlist = list(p["exec_allowlist"])
    return cfg


class AccessPolicy:
    def __init__(self, policy: str = "pairing", allowlist: list[str] | None = None) -> None:
        self.policy = policy
        self.allowlist = allowlist or []

    def can_access(self, user_id: str) -> bool:
        if self.policy == "open":
            return True
        if self.policy == "disabled":
            return False
        return user_id in self.allowlist


class ChannelPolicy:
    def __init__(self, name: str = "telegram", policy: str = "pairing") -> None:
        self.name = name
        self.policy = policy


class PairingManager:
    def __init__(self, pending: list[str] | None = None) -> None:
        self._pending: list[str] = list(pending or [])

    def request(self, user_id: str) -> None:
        if user_id not in self._pending:
            self._pending.append(user_id)

    def approve(self, user_id: str) -> bool:
        if user_id in self._pending:
            self._pending.remove(user_id)
            return True
        return False

    def pending(self) -> list[str]:
        return list(self._pending)
