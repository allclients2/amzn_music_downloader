import requests

url = "https://music.amazon.co.jp/config.json"


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

class Configs:

    @staticmethod
    def fetch_configs(cookieHeader: str):
        headers = {
            "Cookie": cookieHeader,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        }

        response = requests.get(
            url,
            headers=headers
        )

        if response.status_code == 200:
            return response.json()
        else:
            print("error:", response.text)
