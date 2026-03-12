import re

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
