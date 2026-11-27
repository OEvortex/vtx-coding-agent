"""Misc vx bits: brand constants and splash frame shape."""

import time

from vx import brand


class TestBrand:
    def test_program_name(self):
        assert brand.PROGRAM_NAME == "vx"

    def test_version_matches_pyproject(self):
        # Keep in sync with packages/vx/pyproject.toml.
        assert brand.VERSION == "0.1.0"

    def test_italic_brand(self):
        assert brand.ITALIC_BRAND == "𝒗x"


class TestGitBranchCache:
    def test_stale_label_survives_a_refresh(self, monkeypatch, tmp_path):
        # The hint row reads this every frame; blanking it while ``git status``
        # runs was the bottom-bar flicker, so an aged entry must keep serving
        # its last label until the worker lands a new one.
        import vx.app as app

        monkeypatch.setattr(app, "_read_git_branch", lambda cwd: "main*")
        app._BRANCH_STATE.clear()
        app._BRANCH_PENDING.clear()
        cwd = str(tmp_path)
        try:
            assert app._git_branch(cwd) is None  # first call only kicks the fetch
            for _ in range(200):
                if cwd in app._BRANCH_STATE:
                    break
                time.sleep(0.01)
            assert app._git_branch(cwd) == "main*"
            app._BRANCH_STATE[cwd] = ("main*", time.monotonic() - app._BRANCH_TTL - 1)
            assert app._git_branch(cwd) == "main*"
        finally:
            app._BRANCH_STATE.pop(cwd, None)
            app._BRANCH_PENDING.discard(cwd)
