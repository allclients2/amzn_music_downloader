"""Authentication for the Amazon Music downloader.

Builds the single signed `AmazonMusicMobileAPI` session that every stage
(metadata, manifest, license, lyrics) shares. Auth is device-registration OAuth
with RSA-signed requests.

The first run performs an interactive browser sign-in (the user pastes the
post-login URL back). Credentials are pickled to `config/credentials.bin`, keyed
by Amazon `customer_id` so several accounts can be stored side by side (including
multiple in the same region); each account's public metadata (name, country) is
mirrored into `config/config.json`'s `accounts` table for selection/display.
Access tokens are refreshed automatically when they expire.
"""

import os
import pickle
from pathlib import Path

import config
import ui
from amzn_api import AmazonMusicMobileAPI
from amazonmusic.models import (
    AmazonMusicMobileAPICredentials,
    AmazonRegion,
)


class _CompatUnpickler(pickle.Unpickler):
    """Load credential stores written before the move off the `vendor.amazonmusic`
    adapter. Those pickles reference `vendor.amazonmusic.models`, which no longer
    exists, so remap that module onto the submodule's `amazonmusic.models`. The
    store is rewritten under the new path on the next save."""

    def find_class(self, module, name):
        if module == "vendor.amazonmusic" or module.startswith("vendor.amazonmusic."):
            module = "amazonmusic" + module[len("vendor.amazonmusic"):]
        return super().find_class(module, name)

# Lives in the config/ folder (the program is run from the repo root). The env
# override is kept for flexibility.
CREDENTIALS_FILE = Path(os.environ.get("AMZ_CREDENTIALS_FILE", str(config.CREDENTIALS_FILE)))
# Pre-config-folder location; read once so an existing login isn't lost on upgrade.
_LEGACY_CREDENTIALS_FILE = Path("credentials.bin")


def _credentials_path() -> Path:
    """The store to read from: the configured path, or the legacy root file if
    that's the only one present (it migrates to the configured path on save)."""
    if not CREDENTIALS_FILE.exists() and _LEGACY_CREDENTIALS_FILE.exists():
        return _LEGACY_CREDENTIALS_FILE
    return CREDENTIALS_FILE


def _account_key(credentials: AmazonMusicMobileAPICredentials) -> str:
    """The registry/store key for an account: its Amazon `customer_id`, falling
    back to the region's country code on the rare chance the id wasn't recorded."""
    return credentials.customer_id or credentials.account_region.country


def _account_info(credentials: AmazonMusicMobileAPICredentials) -> dict:
    """The public metadata mirrored into `config.json['accounts']` for an account."""
    region = credentials.account_region
    return {
        "name": (credentials.customer_info or {}).get("name") or "Unknown",
        "country": region.country if region else None,
        "region": region.pretty_name if region else None,
    }


def _load_store() -> dict:
    """Return the `{customer_id: AmazonMusicMobileAPICredentials}` store (may be empty).

    Older stores were keyed by country code; they're transparently re-keyed by
    `customer_id` here so several accounts can coexist (including multiple in one
    region). The re-keyed layout is written back on the next save."""
    path = _credentials_path()
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as fh:
            data = _CompatUnpickler(fh).load()
    except Exception as exc:  # corrupt/unreadable store -> treat as logged out
        ui.note(f"warning: could not read {path} ({exc}); ignoring.")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        _account_key(creds): creds
        for creds in data.values()
        if isinstance(creds, AmazonMusicMobileAPICredentials)
    }


def _save_store(store: dict) -> None:
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CREDENTIALS_FILE, "wb") as fh:
        pickle.dump(store, fh)


def _sync_accounts(store: dict) -> None:
    """Mirror the credential store into `config.json['accounts']` (upsert-only;
    writes only when something actually changed, so it's cheap to call on load)."""
    current = config.load_accounts()
    merged = dict(current)
    changed = False
    for account_id, creds in store.items():
        info = _account_info(creds)
        if current.get(account_id) != info:
            merged[account_id] = info
            changed = True
    if changed:
        config.save_accounts(merged)


def _save_account(credentials: AmazonMusicMobileAPICredentials) -> str:
    """Persist one account's credentials and mirror its metadata into config.json.
    Used by both login and token refresh. Returns the account id (customer_id)."""
    account_id = _account_key(credentials)
    store = _load_store()
    store[account_id] = credentials
    _save_store(store)
    _sync_accounts(store)
    return account_id

def _cli_oauth_callback(oauth_url: str, application_name: str, country: str) -> str:
    """Drive the interactive browser OAuth from a terminal.

    Amazon hands out a sign-in URL; the user signs in, lands on a blank/"not
    found" page, and pastes that final URL back here so the auth code can be
    extracted. For JP this is invoked twice (Prime Video first, then Music); each
    call renders its own self-contained screen via `ui.prompt_oauth_url`.
    """
    return ui.prompt_oauth_url(f"{application_name} ({country})", oauth_url)


def login(country: str) -> AmazonMusicMobileAPI:
    """Run the interactive OAuth + device registration for `country` and persist it."""
    country = (country or "").strip().upper()
    if len(country) != 2:
        raise ValueError("Country must be a 2-letter ISO 3166-1 code (e.g. US, GB, JP).")
    # Validate the region up front so a typo fails before the browser step.
    AmazonRegion.get_region_by_country(country)

    ui.note(f"Signing in to Amazon Music for region: {country}")
    inst = AmazonMusicMobileAPI.login_via_mobile(
        email="",
        password="",
        country_code=country,
        # Closure so the callback can title each screen with the region (e.g. JP).
        oauth_flow_callback=lambda url, app: _cli_oauth_callback(url, app, country),
    )

    account_id = _save_account(inst.credentials)
    info = _account_info(inst.credentials)
    ui.note(f"Saved account '{info['name']}' ({info['region']}) [{account_id}].")
    return inst


def _prompt_country() -> str:
    return ui.prompt_region().upper()


def _resolve_account_id(store: dict, account: str | None) -> str | None:
    """Resolve a user-supplied account reference (customer_id, account name, or
    country code) to a stored account id, or None when nothing matches."""
    key = (account or "").strip()
    if not key:
        return None
    if key in store:  # exact customer_id
        return key
    accounts = config.load_accounts()
    for account_id, creds in store.items():
        info = accounts.get(account_id) or _account_info(creds)
        if ((info.get("name") or "").lower() == key.lower()
                or (info.get("country") or "").upper() == key.upper()):
            return account_id
    return None


def delete_account(account: str) -> dict:
    """Remove a stored account (credentials + the config.json mirror) by id, name,
    or country code. Returns the removed account's info; raises KeyError when no
    stored account matches."""
    store = _load_store()
    account_id = _resolve_account_id(store, account)
    if account_id is None:
        raise KeyError(account)

    info = config.load_accounts().get(account_id) or _account_info(store[account_id])
    del store[account_id]
    _save_store(store)

    accounts = config.load_accounts()
    if account_id in accounts:
        del accounts[account_id]
        config.save_accounts(accounts)

    # Drop the default if it pointed at the account we just removed.
    if (config.get_settings().get("default_account") or "") == account_id:
        config.save_setting("default_account", "")
    return info


def _account_label(account_id: str, store: dict, accounts: dict | None = None) -> str:
    """A short human label for an account id, for menus and error messages."""
    accounts = config.load_accounts() if accounts is None else accounts
    info = accounts.get(account_id) or _account_info(store[account_id])
    name = info.get("name") or "Unknown"
    region = info.get("region") or info.get("country") or "?"
    return f"{name} — {region} [{account_id}]"


def _select_credentials(store: dict, account: str | None, country: str | None):
    """Pick stored credentials by explicit account, by region, or by the default/
    sole account. Returns the credentials, or None when the choice is ambiguous or
    the requested region isn't stored yet (the caller decides whether to prompt)."""
    if not store:
        return None

    if account:
        key = account.strip()
        if key in store:  # exact customer_id
            return store[key]
        # Otherwise match by account name (case-insensitive) or country code.
        accounts = config.load_accounts()
        for account_id, creds in store.items():
            info = accounts.get(account_id) or _account_info(creds)
            if ((info.get("name") or "").lower() == key.lower()
                    or (info.get("country") or "").upper() == key.upper()):
                return creds
        labels = ", ".join(_account_label(a, store) for a in store)
        raise RuntimeError(
            f"No stored account matching '{account}'. Stored accounts: {labels}. "
            f"Add one with `python src/main.py account --add`."
        )

    if country:
        target = country.strip().upper()
        for creds in store.values():
            region = creds.account_region
            if region and region.country == target:
                return creds
        return None  # region not stored yet -> caller may sign in for it

    # Nothing requested: use the configured default, then the sole account, else None.
    default = (config.get_settings().get("default_account") or "").strip()
    if default and default in store:
        return store[default]
    if len(store) == 1:
        return next(iter(store.values()))
    return None  # zero, or several accounts with no way to choose


def _prompt_account(store: dict) -> str | None:
    """Interactively choose among several stored accounts. Returns the chosen
    account id, or None to sign in to a brand-new account."""
    accounts = config.load_accounts()
    ids = list(store.keys())
    options = []
    for account_id in ids:
        info = accounts.get(account_id) or _account_info(store[account_id])
        name = info.get("name") or "Unknown"
        region = info.get("region") or info.get("country") or "?"
        options.append((name, region))
    index = ui.prompt_account(options)
    return None if index is None else ids[index]


def get_session(
    account: str | None = None,
    country: str | None = None,
    interactive: bool = True,
) -> AmazonMusicMobileAPI:
    """Return a ready-to-use signed session, signing in interactively if needed.

    `account` selects a stored account by id (customer_id), name, or country code;
    `country` selects by region. With neither, the `default_account` from config is
    used, or the sole stored account, or — when interactive — the user is asked to
    pick one (or sign in). Set `interactive=False` to raise instead of prompting
    when no single account can be resolved.
    """
    store = _load_store()
    _sync_accounts(store)  # keep config.json's accounts table current

    credentials = _select_credentials(store, account, country)

    if credentials is None:
        if not interactive:
            if store:
                labels = ", ".join(_account_label(a, store) for a in store)
                raise RuntimeError(
                    "Multiple Amazon Music accounts are stored and none was selected. "
                    f"Set `default_account` in config/config.json (one of: {labels})."
                )
            raise RuntimeError(
                "No saved Amazon Music login. Run `python src/main.py account --add` "
                "once to sign in."
            )
        # Interactive: choose among existing accounts, or sign in to a new one.
        if store and account is None and country is None:
            chosen = _prompt_account(store)
            if chosen is not None:
                credentials = store[chosen]
            else:
                return login(_prompt_country())
        else:
            if not store:
                ui.note("No saved Amazon Music login found.")
            return login(country or _prompt_country())

    return _ready_session(credentials)


def _ready_session(credentials) -> AmazonMusicMobileAPI:
    """Wrap stored credentials in a session, refreshing an expired access token."""
    session = AmazonMusicMobileAPI(credentials=credentials)
    if session.credentials.access_token_expired:
        ui.note("Access token expired; refreshing...")
        session.refresh_access_token(force=True)
        _save_account(session.credentials)
    return session
