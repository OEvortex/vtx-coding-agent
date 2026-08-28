"""Built-in skill discovery for openjarvis."""

from pathlib import Path

# Bundled skills ship inside the openjarvis package; the filesystem tool
# grants read access to this directory so the agent can consult SKILL.md files.
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

__all__ = ["BUILTIN_SKILLS_DIR"]
