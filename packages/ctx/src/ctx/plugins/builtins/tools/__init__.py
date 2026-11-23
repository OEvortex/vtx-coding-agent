"""Shared helpers for tool plugin rows.

The harness ships no concrete tools, so there are no built-in tool rows here —
``_register`` is the mount helper every product tool row uses to register on
``ctx.tools`` reversibly. A row that mounts a tool owned by a product lives
with that product, so the dependency points from the product into the plugin
system rather than the other way round.
"""

from __future__ import annotations
