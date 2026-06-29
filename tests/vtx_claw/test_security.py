from __future__ import annotations

import pytest

from vtx_claw.auth.policies import apply_preset
from vtx_claw.config.schema import SecurityConfig


@pytest.mark.parametrize("preset", ["relaxed", "trusted", "standard", "strict"])
def test_all_presets_apply(preset: str):
    cfg = SecurityConfig()
    apply_preset(cfg, preset)
    assert cfg.default_preset == preset


def test_relaxed_preset_enables_full_exec():
    cfg = SecurityConfig()
    apply_preset(cfg, "relaxed")
    assert cfg.exec_policy == "full"
    assert cfg.safe_bins == []


def test_standard_preset_uses_allowlist():
    cfg = SecurityConfig()
    apply_preset(cfg, "standard")
    assert cfg.exec_policy == "on-miss"
    assert "python" in cfg.safe_bins


def test_strict_preset_denies_exec():
    cfg = SecurityConfig()
    apply_preset(cfg, "strict")
    assert cfg.exec_policy == "deny"
    assert cfg.safe_bins == []


def test_unknown_preset_raises():
    cfg = SecurityConfig()
    with pytest.raises(ValueError):
        apply_preset(cfg, "invalid")
