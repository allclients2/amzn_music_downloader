import requests
from playwright.async_api import async_playwright

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


async def build_browser_with_cookies(session: requests.Session):

    playwright = await async_playwright().start()

    browser = await playwright.chromium.launch(
        headless=False
    )

    context = await browser.new_context(
        user_agent=UA
    )

    cookies = _session_to_playwright_cookies(session)

    if cookies:
        print("\nInjecting cookies into browser context...")
        await context.add_cookies(cookies)

    page = await context.new_page()

    return {
        "playwright": playwright,
        "browser": browser,
        "context": context,
        "page": page
    }


async def fetch_configs(browser_session):

    page = browser_session["page"]

    print("\n=== Navigating to Amazon Music player ===")

    async with page.expect_response(
        lambda r: "/config.json" in r.url,
        timeout=15000
    ) as config_response:

        await page.goto(
            "https://music.amazon.co.jp/home",
            wait_until="domcontentloaded"
        )

    await page.wait_for_timeout(5000)

    response = await config_response.value

    print("\n=== config.json captured ===")

    config = await response.json()

    print("\nconfig:", config)

    return config