"""Authentication for the Amazon Music downloader.

Builds the single signed `AmazonMusicMobileAPI` session that every stage
(metadata, manifest, license, lyrics) shares. Auth is device-registration OAuth
with RSA-signed requests.

The first run performs an interactive browser sign-in (the user pastes the
post-login URL back). Credentials are pickled to `config/credentials.bin`, keyed
by country so several regions can be stored side by side, and the access token is
refreshed automatically when it expires.
"""

import os
import sys
import termios
import pickle
from pathlib import Path

import config
from vendor.amazonmusic.azapi import AmazonMusicMobileAPI
from vendor.amazonmusic.models import (
    AmazonMusicMobileAPICredentials,
    AmazonRegion,
)

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


def _load_store() -> dict:
    """Return the `{country: AmazonMusicMobileAPICredentials}` store (may be empty)."""
    path = _credentials_path()
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as fh:
            data = pickle.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # corrupt/unreadable store -> treat as logged out
        print(f"warning: could not read {path} ({exc}); ignoring.")
        return {}


def _save_store(store: dict) -> None:
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CREDENTIALS_FILE, "wb") as fh:
        pickle.dump(store, fh)

def read_long_line(prompt=""):
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        new = termios.tcgetattr(fd)
        new[3] &= ~termios.ICANON  # lflags: disable canonical (line-buffered) mode
        termios.tcsetattr(fd, termios.TCSANOW, new)
        chars = []
        while True:
            c = sys.stdin.read(1)
            if c in ("\n", "\r"):
                break
            chars.append(c)
        return "".join(chars)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        sys.stdout.write("\n")
        sys.stdout.flush()

def _cli_oauth_callback(oauth_url: str, application_name: str) -> str:
    """Drive the interactive browser OAuth from a terminal.

    Amazon hands out a sign-in URL; the user signs in, lands on a blank/"not
    found" page, and pastes that final URL back here so the auth code can be
    extracted. For JP this is invoked twice (Prime Video first, then Music).
    """
    print()
    print("=" * 70)
    print(f"Sign in to: {application_name}")
    print("1. Open this URL in your browser and sign in:")
    print()
    print(f"   {oauth_url}")
    print()
    print("2. After signing in you'll land on a blank / 'page not found' page.")
    print("3. Copy that page's FULL URL from the address bar and paste it below.")
    print("=" * 70)
    callback_url = read_long_line("Paste the post-login URL here: ").strip()
    return callback_url


def login(country: str) -> AmazonMusicMobileAPI:
    """Run the interactive OAuth + device registration for `country` and persist it."""
    country = (country or "").strip().upper()
    if len(country) != 2:
        raise ValueError("Country must be a 2-letter ISO 3166-1 code (e.g. US, GB, JP).")
    # Validate the region up front so a typo fails before the browser step.
    AmazonRegion.get_region_by_country(country)

    print(f"Signing in to Amazon Music for region: {country}")
    inst = AmazonMusicMobileAPI.login_via_mobile(
        email="",
        password="",
        country_code=country,
        oauth_flow_callback=_cli_oauth_callback,
    )

    store = _load_store()
    store[inst.credentials.account_region.country] = inst.credentials
    _save_store(store)
    print(f"Saved credentials for {inst.credentials.account_region.pretty_name}.")
    return inst


def _prompt_country() -> str:
    print("No saved Amazon Music login found.")
    return input("Enter your 2-letter region code (e.g. US, GB, DE, JP): ").strip().upper()


def get_session(country: str | None = None, interactive: bool = True) -> AmazonMusicMobileAPI:
    """Return a ready-to-use signed session, signing in interactively if needed.

    `country` selects which stored login to use (for multi-region stores). When
    omitted, the only stored region is used, or the user is prompted to sign in.
    Set `interactive=False` (e.g. in the bot worker subprocess) to raise instead
    of prompting when no usable credentials are stored.
    """
    store = _load_store()

    if country:
        country = country.strip().upper()
        credentials = store.get(country)
    elif len(store) == 1:
        credentials = next(iter(store.values()))
    else:
        credentials = None

    if credentials is None:
        # Either nothing stored, or the requested region isn't stored yet.
        if not interactive:
            raise RuntimeError(
                "No saved Amazon Music login. Run `python src/main.py <asin>` "
                "once to sign in before using the bot."
            )
        target = country or _prompt_country()
        return login(target)

    session = AmazonMusicMobileAPI(credentials=credentials)
    if session.credentials.access_token_expired:
        print("Access token expired; refreshing...")
        session.refresh_access_token(force=True)
        store[session.credentials.account_region.country] = session.credentials
        _save_store(store)
    return session
