"""Resolve the package version.

Prefers the installed distribution's metadata (`importlib.metadata`); for a bare
source checkout that was never installed, falls back to reading `pyproject.toml`
at the repo root (two levels up: amzdl/ -> src/ -> repo root).
"""

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_DIST_NAME = "amzdl"


def _from_pyproject() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "0+unknown"
    match = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else "0+unknown"


try:
    VERSION = version(_DIST_NAME)
except PackageNotFoundError:
    VERSION = _from_pyproject()
