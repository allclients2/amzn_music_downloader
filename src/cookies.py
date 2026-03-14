import time
from typing import Dict, Tuple, List

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None


REQUIRED_ORDER = [
    "am-loader-experiment",
    "cwr_u",
    "session-id",
    "ubid-acbjp",
    "lc-acbjp",
    "sso-state-acbjp",
    "x-acbjp",
    "at-acbjp",
    "sess-at-acbjp",
    "sst-acbjp",
    "session-id-time",
    "am-token",
    "cwr_s",
    "session-token",
]

class CookieError(Exception):
    pass

def _validate_required(
    parsed: Dict[str, str],
    expired: List[str]
):
    missing = [c for c in REQUIRED_ORDER if c not in parsed]

    if expired:
        raise CookieError(
            f"❌ The following required cookies are EXPIRED:\n"
            f"{expired}\n\n"
            f"Please refresh your login session and export cookies again."
        )

    if missing:
        raise CookieError(
            f"❌ Missing required cookies:\n"
            f"{missing}\n\n"
            f"Make sure you're logged in and exported the correct domain."
        )


async def get_cookie_header(browser_session) -> str:
    context = browser_session["context"]
    cookies = await context.cookies("https://music.amazon.co.jp")

    filtered = {
        c["name"]: c["value"]
        for c in cookies
        if c["name"] in REQUIRED_ORDER
        and (c.get("expires", 0) == 0 or c.get("expires", 0) > time.time())
    }

    _validate_required(filtered, [
        c["name"] for c in cookies
        if c["name"] in REQUIRED_ORDER
        and c.get("expires", 0) != 0
        and c["expires"] < time.time()
    ])

    return "; ".join(f"{name}={filtered[name]}" for name in REQUIRED_ORDER if name in filtered)

def cookies_from_browser(domain: str, browser: str = "chrome") -> str:
    if browser_cookie3 is None:
        raise CookieError(
            "browser-cookie3 is not installed.\n"
            "Install it with: pip install browser-cookie3"
        )

    try:
        if browser == "chrome":
            jar = browser_cookie3.chrome(domain_name=domain)
        elif browser == "edge":
            jar = browser_cookie3.edge(domain_name=domain)
        elif browser == "firefox":
            jar = browser_cookie3.firefox(domain_name=domain)
        else:
            raise CookieError(f"Unsupported browser: {browser}")
    except Exception as e:
        raise CookieError(f"Failed to load cookies from browser: {e}")

    parsed = {}
    expired = []

    now = time.time()

    for cookie in jar:
        if cookie.name not in REQUIRED_ORDER:
            continue

        if cookie.expires and cookie.expires < now:
            expired.append(cookie.name)
            continue

        parsed[cookie.name] = cookie.value

    _validate_required(parsed, expired)

    ordered_pairs = [
        f"{name}={parsed[name]}"
        for name in REQUIRED_ORDER
    ]

    return "; ".join(ordered_pairs)