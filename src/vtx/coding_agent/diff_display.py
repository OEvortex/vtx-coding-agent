DIFF_BG_PAD_MARKER = "__VTX_DIFF_BG_PAD__"


def blend_hex(fg_hex: str, bg_hex: str, alpha: float = 0.15) -> str:
    fg_hex = fg_hex.lstrip("#")
    bg_hex = bg_hex.lstrip("#")
    fr, fg, fb = int(fg_hex[:2], 16), int(fg_hex[2:4], 16), int(fg_hex[4:6], 16)
    br, bg_c, bb = int(bg_hex[:2], 16), int(bg_hex[2:4], 16), int(bg_hex[4:6], 16)
    r = int(fr * alpha + br * (1 - alpha))
    g = int(fg * alpha + bg_c * (1 - alpha))
    b = int(fb * alpha + bb * (1 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"
