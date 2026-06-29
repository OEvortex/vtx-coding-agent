try:
    from importlib.metadata import version

    VERSION = version("vtx-claw")
except Exception:
    VERSION = "0.1.0"
