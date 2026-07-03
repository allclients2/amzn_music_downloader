import re
import string
import unicodedata
from pathlib import Path

ILLEGAL_CHARS_RE = r'[\\/:*?"<>|;]'

DEFAULT_FOLDER_TEMPLATE = "{album_artist}/{album}"
DEFAULT_FILE_TEMPLATE = "{disc} - {track} {title}"
DEFAULT_MULTI_DISC_FILE_TEMPLATE = "{disc} - {track} {title}"

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


class _TemplateFormatter(string.Formatter):
    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            return kwargs.get(key, (None, ""))
        return super().get_value(key, args, kwargs)

    def format_field(self, value, format_spec):
        if isinstance(value, tuple) and len(value) == 2:
            actual, fallback = value
            if actual is None or actual == "":
                return str(fallback)
            try:
                return super().format_field(actual, format_spec)
            except (ValueError, TypeError):
                return str(fallback)
        return super().format_field(value, format_spec)


def _track_template_fields(track) -> dict:
    date = track.release_date or ""
    return {
        "album_artist": (track.album_artist, "Unknown Artist"),
        "album": (track.album_name, "Unknown Album"),
        "artist": (track.artist, "Unknown Artist"),
        "title": (track.title, "Unknown Title"),
        "track": (track.track_number, ""),
        "track_abs": (track.abs_track_number or track.track_number, ""),
        "track_total": (track.total_tracks, ""),
        "disc": (track.disc, ""),
        "disc_total": (track.total_discs, ""),
        "date": (track.release_date, ""),
        "year": (date[:4] or None, ""),
        "genre": (track.genre, ""),
        "isrc": (track.isrc, ""),
        "asin": (track.asin, ""),
        "album_asin": (track.album_asin, ""),
    }


def render_track_relpath(track, folder_template: str, file_template: str) -> Path:
    fmt = _TemplateFormatter()
    fields = _track_template_fields(track)
    raw_parts = []
    if folder_template:
        raw_parts.extend(p for p in folder_template.split("/") if p.strip())
    raw_parts.extend(p for p in file_template.split("/") if p.strip())
    if not raw_parts:
        raw_parts = [DEFAULT_FILE_TEMPLATE]
    last = len(raw_parts) - 1
    parts = [
        safe_filename(fmt.format(raw, **fields), has_file_ext=(i == last))
        for i, raw in enumerate(raw_parts)
    ]
    return Path(*[p for p in parts if p])


def char_width(c: str) -> int:
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def disp_width(s: str) -> int:
    return sum(char_width(c) for c in s)


def take_cols(s: str, max_width: int) -> str:
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
    t = truncate(s, width)
    return t + " " * (width - disp_width(t))
