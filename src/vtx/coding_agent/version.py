"""Re-export the package version from the harness.

The resolution logic is product-neutral and lives in
:mod:`vtx.ai.agent.version`; the coding agent keeps its public import
path for backwards compatibility.
"""

from vtx.ai.agent.version import PACKAGE_NAME, VERSION, format_version

__all__ = ["PACKAGE_NAME", "VERSION", "format_version"]
