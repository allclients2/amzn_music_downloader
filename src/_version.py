import re
from pathlib import Path

# pyproject.toml lives at the repo root, one level above this src/ directory.
_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

_text = _PYPROJECT.read_text(encoding="utf-8")
_match = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', _text)
VERSION = _match.group(1) if _match else "0+unknown"
