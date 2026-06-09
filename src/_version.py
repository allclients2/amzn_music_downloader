import tomllib
from pathlib import Path

# pyproject.toml lives at the repo root, one level above this src/ directory.
_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

with _PYPROJECT.open("rb") as f:
    VERSION = tomllib.load(f)["project"]["version"]
