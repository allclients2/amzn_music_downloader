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


class Cookies:

    @staticmethod
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

    @staticmethod
    def netscape_to_cookie_header(file_path: str) -> str:
        parsed_cookies: Dict[str, str] = {}
        expired_cookies: List[str] = []

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")
                if len(parts) < 7:
                    continue

                domain, flag, path, secure, expires, name, value = parts[:7]

                if name not in REQUIRED_ORDER:
                    continue

                try:
                    exp = float(expires)
                    if exp != 0 and exp < time.time():
                        expired_cookies.append(name)
                        continue
                except ValueError:
                    pass

                parsed_cookies[name] = value

        Cookies._validate_required(parsed_cookies, expired_cookies)

        ordered_pairs = [
            f"{name}={parsed_cookies[name]}"
            for name in REQUIRED_ORDER
        ]

        return "; ".join(ordered_pairs)

    # ---------------------------
    # 🔥 Browser Auto Extraction
    # ---------------------------

    @staticmethod
    def from_browser(domain: str, browser: str = "chrome") -> str:
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

        Cookies._validate_required(parsed, expired)

        ordered_pairs = [
            f"{name}={parsed[name]}"
            for name in REQUIRED_ORDER
        ]

        return "; ".join(ordered_pairs)