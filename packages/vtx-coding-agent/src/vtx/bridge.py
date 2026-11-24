"""vtx compatibility bridge — forwards all arguments to the ctx console script."""

from __future__ import annotations

import warnings

from ctx.code.cli import main as ctx_main


def main() -> None:
    warnings.warn(
        "The 'vtx' command is deprecated. Install 'ctx-agent' and use 'ctx' instead.",
        DeprecationWarning,
        stacklevel=1,
    )
    ctx_main()


if __name__ == "__main__":
    main()
