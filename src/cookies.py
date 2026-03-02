import time

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

class Cookies:
    @staticmethod
    def netscape_to_cookie_header(file_path: str) -> str:
        parsed_cookies = {}

        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()

                print("parsing line:", line)

                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")
                if len(parts) < 7:
                    continue

                domain, flag, path, secure, expires, name, value = parts[:7]

                # Skip expired cookies
                try:
                    if float(expires) != 0 and float(expires) < time.time():
                        continue
                except ValueError:
                    pass

                if name in REQUIRED_ORDER:
                    parsed_cookies[name] = value

        # Assert all required cookies exist
        missing = [name for name in REQUIRED_ORDER if name not in parsed_cookies]
        assert not missing, f"Missing required cookies: {missing}"

        # Build header in exact required order
        ordered_pairs = [f"{name}={parsed_cookies[name]}" for name in REQUIRED_ORDER]

        return "; ".join(ordered_pairs)