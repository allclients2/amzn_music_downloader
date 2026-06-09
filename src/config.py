"""Persistent configuration for the downloader.

On first use a `config/` folder is created at the repo root (the program is always
run from the root) holding:

  - `config.json`     — user-editable defaults (quality, output dir, wvd path)
  - `credentials.bin` — pickled per-region Amazon Music logins (written by `auth`)

`accounts` is reserved for later multi-account switching (per-account country
code, credential files, …); it's generated empty and otherwise untouched for now.
"""

import copy
import json
from pathlib import Path

CONFIG_DIR = Path("config")
CONFIG_FILE = CONFIG_DIR / "config.json"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.bin"

DEFAULT_CONFIG = {
    "config": {
        "default_quality": "HD",
        "default_output": "output",
        "default_wvd_path": "device.wvd",
    },
    "accounts": {},
}


def _write_default() -> dict:
    """Create `config/config.json` from the defaults and return a fresh copy."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(DEFAULT_CONFIG, fh, indent=4)
        fh.write("\n")
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config() -> dict:
    """Return the config dict, generating `config/config.json` from defaults when
    it's missing. Missing `config` keys are backfilled from the defaults so an
    older/partial file keeps working; `accounts` is preserved as-is."""
    if not CONFIG_FILE.exists():
        return _write_default()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"warning: could not read {CONFIG_FILE} ({exc}); using defaults.")
        return copy.deepcopy(DEFAULT_CONFIG)

    merged = copy.deepcopy(DEFAULT_CONFIG)
    if isinstance(data, dict):
        if isinstance(data.get("config"), dict):
            merged["config"].update(data["config"])
        if isinstance(data.get("accounts"), dict):
            merged["accounts"] = data["accounts"]
    return merged


def get_settings() -> dict:
    """The `config` sub-table: default_quality / default_output / default_wvd_path."""
    return load_config()["config"]
