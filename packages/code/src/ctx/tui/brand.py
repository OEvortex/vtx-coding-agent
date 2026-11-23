"""The CTX wordmark, shared by the exit summary and the TUI header.

One definition, because the two places that print it disagreeing is exactly the
kind of drift nobody notices until the brand looks broken in one of them.
"""

CTX_LOGO = [
    "██╗   ██╗████████╗██╗  ██╗",
    "██║   ██║╚══██╔══╝╚██╗██╔╝",
    "██║   ██║   ██║    ╚███╔╝ ",
    "╚██╗ ██╔╝   ██║    ██╔██╗ ",
    " ╚████╔╝    ██║   ██╔╝ ██╗",
    "  ╚═══╝     ╚═╝   ╚═╝  ╚═╝",
]

__all__ = ["CTX_LOGO"]
