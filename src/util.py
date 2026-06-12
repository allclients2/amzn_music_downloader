import re
import unicodedata

ILLEGAL_CHARS_RE = r'[\\/:*?"<>|;]'

def safe_filename(name, has_file_ext=False):
    sanitized = re.sub(ILLEGAL_CHARS_RE, "_", name)
    if not has_file_ext:
        if sanitized.endswith(".."):
            sanitized = sanitized[:-1] + "_"
        elif sanitized.endswith("."):
            sanitized = sanitized[:-1]
    return sanitized.strip()

def build_output_filename(disc_number: str, track_num: int, track_name: str) -> str:
    filename = f"{disc_number} - {track_num} {track_name}"
    return safe_filename(filename, True)


# ── display-width helpers ─────────────────────────────────────────────────────
# Shared by the CLI's two renderers (`ui` static screens, `progress` animated bar)
# so CJK/full-width characters are counted as 2 columns consistently everywhere.
def char_width(c: str) -> int:
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def disp_width(s: str) -> int:
    """Display width, counting CJK/full-width characters as 2 columns."""
    return sum(char_width(c) for c in s)


def take_cols(s: str, max_width: int) -> str:
    """Leading slice of `s` that fits in `max_width` columns (no ellipsis)."""
    if max_width <= 0:
        return ""
    out, width = "", 0
    for c in s:
        cw = char_width(c)
        if width + cw > max_width:
            break
        out += c
        width += cw
    return out


def truncate(s: str, max_width: int) -> str:
    """Truncate `s` to `max_width` columns, appending an ellipsis when clipped."""
    if max_width <= 0:
        return ""
    if disp_width(s) <= max_width:
        return s
    out, width = "", 0
    for c in s:
        cw = char_width(c)
        if width + cw > max_width - 1:
            break
        out += c
        width += cw
    return out + "…"


def fixed(s: str, width: int) -> str:
    """Truncate `s` to `width` columns, then pad with spaces to exactly `width`."""
    t = truncate(s, width)
    return t + " " * (width - disp_width(t))
