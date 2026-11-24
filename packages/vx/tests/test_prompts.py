"""Prompt registration: idempotent install of vx's base persona."""

from vx import prompts


class TestRegister:
    def test_registers_once(self, monkeypatch):
        calls = []
        # vx.prompts binds the function at import time, so patch it there.
        monkeypatch.setattr(prompts, "register_base_prompt", lambda fn: calls.append(fn))
        prompts._registered = False
        try:
            prompts.register()
            prompts.register()
        finally:
            prompts._registered = False
        assert len(calls) == 1
        # The registered factory returns vx's persona.
        assert "vx" in calls[0]()

    def test_base_prompt_content(self):
        assert "You are vx" in prompts.VX_BASE
        assert "apply_patch" in prompts.VX_BASE

    def test_laziness_is_scoped_to_implementation(self):
        assert "Laziness governs what you write, never how you read" in prompts.VX_BASE
        assert "Laziness is for the diff, not the investigation." in prompts.VX_BASE
