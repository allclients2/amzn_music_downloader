"""Persistent configuration for the downloader.

On first use a `config/` folder is created at the repo root (the program is always
run from the root) holding:

  - `config.json`     — user-editable defaults (quality, output dir, wvd path)
  - `credentials.bin` — pickled per-account Amazon Music logins (written by `auth`)

The `accounts` table is the registry of signed-in accounts, keyed by the account's
`customer_id` (the stable, unique Amazon account identifier). Each entry mirrors the
human-readable metadata of the matching credentials in `credentials.bin`:

    "accounts": {
        "<customer_id>": {
            "name": "Jane Doe",                       # account holder name
            "country": "US",                          # 2-letter region code
            "region": "United States of America"      # pretty region name
        }
    }

`auth` keeps this table in sync with `credentials.bin` on login/refresh; it is
written for display/selection and is safe to read but should not be hand-edited to
add accounts (the secrets live in `credentials.bin`). `default_account` (a
`customer_id`) picks which account to use when several are stored and none is
requested explicitly.
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
        "default_account": "",
        "default_concurrency": 5,
        "default_search_limit": 8,
    },
    "accounts": {},
}


def _write_config(data: dict) -> None:
    """Write the full config dict to `config/config.json`."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4)
        fh.write("\n")


def _write_default() -> dict:
    """Create `config/config.json` from the defaults and return a fresh copy."""
    _write_config(DEFAULT_CONFIG)
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
    """The `config` sub-table: default_quality / default_output / default_wvd_path /
    default_account / default_concurrency."""
    return load_config()["config"]


def load_accounts() -> dict:
    """The `accounts` registry: `{customer_id: {name, country, region}}` (may be empty)."""
    return load_config()["accounts"]


def save_accounts(accounts: dict) -> None:
    """Replace the `accounts` table, preserving the rest of the config file."""
    data = load_config()
    data["accounts"] = accounts
    _write_config(data)


def save_setting(key: str, value) -> None:
    """Set one key in the `config` sub-table, preserving the rest of the file."""
    data = load_config()
    data["config"][key] = value
    _write_config(data)
