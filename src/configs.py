import requests
from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"


def _session_to_playwright_cookies(session: requests.Session):

    cookies = []

    print("\n=== Loading cookies from requests session ===")

    for c in session.cookies:

        if "amazon.co.jp" not in c.domain and "music.amazon.co.jp" not in c.domain:
            continue

        print(f"{c.name}={c.value} domain={c.domain}")

        cookie = {
            "name": c.name,
            "value": c.value,
            "domain": ".amazon.co.jp",
            "path": c.path or "/",
            "httpOnly": c._rest.get("HttpOnly", False),
            "secure": True
        }

        if c.expires:
            cookie["expires"] = int(c.expires)

        cookies.append(cookie)

    return cookies


def build_browser_with_cookies(session: requests.Session):

    playwright = sync_playwright().start()

    browser = playwright.chromium.launch(
        headless=True
    )

    context = browser.new_context(
        user_agent=UA
    )

    cookies = _session_to_playwright_cookies(session)

    if cookies:
        print("\nInjecting cookies into browser context...")
        context.add_cookies(cookies)

    page = context.new_page()

    return {
        "playwright": playwright,
        "browser": browser,
        "context": context,
        "page": page
    }


def fetch_configs(browser_session):

    page = browser_session["page"]

    print("\n=== Navigating to Amazon Music player ===")

    with page.expect_response(
        lambda r: "/config.json" in r.url,
        timeout=15000
    ) as config_response:

        page.goto(
            "https://music.amazon.co.jp/home",
            wait_until="domcontentloaded"
        )

    response = config_response.value

    print("\n=== config.json captured ===")

    config = response.json()

    print("\nconfig:", config)

    return config
