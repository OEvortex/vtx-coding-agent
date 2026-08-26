import base64
import binascii
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile


def read_clipboard_image() -> tuple[bytes, str] | None:
    """Read image data from the system clipboard.

    Returns (raw_bytes, mime_type), or None when the clipboard holds no
    image (or no platform tool is available). Raw bytes may be any of the
    formats sniffed by _sniff_image_mime.
    """
    if sys.platform == "darwin":
        return _macos_clipboard_image()

    if sys.platform == "win32":
        return _windows_clipboard_image()

    if _is_wayland_session():
        image = _capture_image(["wl-paste", "--type", "image/png"])
        if image:
            return image

    targets = _try_capture(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"])
    if targets is not None:
        for line in targets.decode(errors="replace").splitlines():
            target = line.strip()
            if not target.startswith("image/"):
                continue
            image = _capture_image(["xclip", "-selection", "clipboard", "-t", target, "-o"])
            if image:
                return image

    return None


def copy_to_clipboard(text: str) -> None:
    encoded = base64.b64encode(text.encode()).decode()
    print(f"\033]52;c;{encoded}\a", end="", flush=True)

    if sys.platform == "darwin":
        _try_run(["pbcopy"], text)
        return

    if sys.platform == "win32":
        _try_run(["clip"], text)
        return

    if os.environ.get("TERMUX_VERSION") and _try_run(["termux-clipboard-set"], text):
        return

    if _is_wayland_session():
        if _try_run(["wl-copy"], text):
            return
        if _try_run(["xclip", "-selection", "clipboard"], text):
            return
        _try_run(["xsel", "--clipboard", "--input"], text)
        return

    if _try_run(["xclip", "-selection", "clipboard"], text):
        return
    _try_run(["xsel", "--clipboard", "--input"], text)


def _is_wayland_session() -> bool:
    return (
        bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    )


def _try_run(command: list[str], text: str) -> bool:
    if shutil.which(command[0]) is None:
        return False

    try:
        subprocess.run(
            command,
            input=text,
            text=True,
            check=True,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return True


def _try_capture(command: list[str]) -> bytes | None:
    """Run a command and return stdout bytes, or None if unavailable/failed."""
    if shutil.which(command[0]) is None:
        return None

    try:
        result = subprocess.run(
            command, check=True, timeout=5, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return None

    return result.stdout


def _capture_image(command: list[str]) -> tuple[bytes, str] | None:
    data = _try_capture(command)
    if not data:
        return None
    mime_type = _sniff_image_mime(data)
    if mime_type is None:
        return None
    return data, mime_type


def _macos_clipboard_image() -> tuple[bytes, str] | None:
    """Dump the clipboard's PNG flavor to a temp file via AppleScript."""
    if shutil.which("osascript") is None:
        return None

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    script = (
        f'set outFile to POSIX file "{path}"\n'
        "set fh to open for access outFile with write permission\n"
        "set eof fh to 0\n"
        "write (the clipboard as \u00abclass PNGf\u00bb) to fh\n"
        "close access fh\n"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with open(path, "rb") as f:
            data = f.read()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)

    mime_type = _sniff_image_mime(data)
    if mime_type is None:
        return None
    return data, mime_type


def _windows_clipboard_image() -> tuple[bytes, str] | None:
    """Read the clipboard image as PNG via PowerShell, base64 on stdout."""
    script = (
        "$img = Get-Clipboard -Format Image; "
        "if ($img -ne $null) { "
        "$ms = New-Object System.IO.MemoryStream; "
        "$img.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png); "
        "Write-Output ([Convert]::ToBase64String($ms.ToArray())) }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            check=True,
            timeout=10,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    try:
        data = base64.b64decode(result.stdout.strip())
    except (ValueError, binascii.Error):
        return None

    mime_type = _sniff_image_mime(data)
    if mime_type is None:
        return None
    return data, mime_type


def _sniff_image_mime(data: bytes) -> str | None:
    """Detect image MIME from magic bytes."""
    if not data:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    return None
