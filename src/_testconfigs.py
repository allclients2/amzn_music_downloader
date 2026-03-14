from configs import fetch_configs, build_browser_with_cookies
from main import load_cookie_session
import base64
import json
import urllib.parse


def validate_am_token(session):
    for c in session.cookies:
        if c.name == "am-token":
            token = urllib.parse.unquote(c.value)  # decode %3D etc
            data = json.loads(base64.b64decode(token).decode())

            print("decoded am-token:", data)

            return data.get("profileId") != ""

    return False


session, jar = load_cookie_session("cookies.txt")

print("prefetch am-token test...")
if not validate_am_token(session):
    raise Exception("am-token missing music profile")
else:
    print("validate_am_token(session): passed")

browser = build_browser_with_cookies(session)

result = fetch_configs(browser)

print("configs loaded")
if jar:
    print("saved cookies")
    jar.save(ignore_discard=True, ignore_expires=True)

browser["browser"].close()
browser["playwright"].stop()
