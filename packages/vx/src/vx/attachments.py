"""Images a prompt carries: pasted from the clipboard or named with ``@``.

The agent loop already accepts ``list[ImageContent]`` beside the prompt text
(``ctx.agent.loop.Agent.run``), so all vx has to do is collect them. A prompt
can hold several images; the composer shows one chip per image and the turn's
rail repeats them, so a reader can see what the model was shown.
"""

from __future__ import annotations

import base64
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotation only: ctx.protocol drags aiohttp and ~200 ms of models in.
    from ctx.protocol.types import ImageContent

#: Image extensions ``@`` attachment recognises. Mirrors ctx's read tool.
IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif", ".heic", ".heif"}
)

#: The suffix a clipboard image is named with.
_MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


@dataclass
class Attachment:
    """One image bound to the next prompt."""

    data: str  # base64, as the provider expects it
    mime_type: str
    name: str = ""
    source: str = ""  # "clipboard" | "file"

    @property
    def label(self) -> str:
        return self.name or self.mime_type

    @property
    def size_label(self) -> str:
        try:
            raw = base64.b64decode(self.data, validate=False)
        except Exception:
            return ""
        n = len(raw)
        if n >= 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n} B"

    def content(self) -> ImageContent:
        from ctx.protocol.types import ImageContent

        return ImageContent(data=self.data, mime_type=self.mime_type)


def from_clipboard() -> Attachment | None:
    """The clipboard's image, if it holds one and a tool can read it."""
    try:
        from ctx.term.clipboard import read_clipboard_image
    except Exception:
        return None
    try:
        found = read_clipboard_image()
    except Exception:
        return None
    if not found:
        return None
    raw, mime = found
    if not raw:
        return None
    # A clipboard screenshot can be far larger than a model accepts; file
    # attachments already pass through this, so pasted ones do too.
    with suppress(Exception):
        from ctx.core.image import resize_image

        raw, mime, _note = resize_image(raw, mime)
    ext = _MIME_SUFFIX.get(mime, ".png")
    return Attachment(
        data=base64.b64encode(raw).decode("ascii"),
        mime_type=mime or "image/png",
        name=f"pasted{ext}",
        source="clipboard",
    )


def clipboard_text() -> str:
    """The clipboard's text, for a ctrl+v that holds no image."""
    try:
        from ctx.term.clipboard import _try_capture
    except Exception:
        return ""
    for command in (
        ["wl-paste", "--no-newline"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ):
        with suppress(Exception):
            raw = _try_capture(command)
            if raw:
                return raw.decode("utf-8", errors="replace")
    return ""


def from_path(path: str, cwd: str) -> Attachment | None:
    """An image file as an attachment, downscaled the way the read tool does."""
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = Path(cwd) / resolved
    try:
        resolved = resolved.expanduser().resolve()
    except Exception:
        return None
    if not resolved.is_file():
        return None
    from ctx.core.image import is_image_file, read_and_process_image

    if not is_image_file(str(resolved)):
        return None
    try:
        data, mime, _note = read_and_process_image(str(resolved))
    except Exception:
        return None
    return Attachment(data=data, mime_type=mime, name=resolved.name, source="file")


def image_paths_in(text: str, cwd: str) -> list[str]:
    """Image files a prompt names, so ``@shot.png`` attaches without extra keys.

    Only tokens that start with ``@`` and name an existing image count, which
    keeps ordinary prose about ``.png`` from attaching anything.
    """
    found: list[str] = []
    for token in text.split():
        if not token.startswith("@") or len(token) < 2:
            continue
        candidate = token[1:].strip(",.;:)\"'")
        if not candidate:
            continue
        suffix = os.path.splitext(candidate)[1].lower()
        if suffix not in IMAGE_SUFFIXES:
            continue
        path = Path(cwd) / os.path.expanduser(candidate)
        if path.is_file():
            found.append(candidate)
    return found


def describe(attachments: list[Attachment]) -> str:
    """A one-line summary of a prompt's images, for the transcript rail."""
    if not attachments:
        return ""
    parts = [f"{a.label} {a.size_label}".strip() for a in attachments]
    return "image: " + ", ".join(parts)
