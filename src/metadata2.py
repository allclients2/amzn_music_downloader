import json
import requests

url = "https://fe.web.skill.music.a2z.com/api/showLyrics"

headers = {
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": "https://music.amazon.co.jp",
    "Referer": "https://music.amazon.co.jp/",
    "User-Agent": "Mozilla/5.0",
}

# 1️⃣ Authentication JSON (must be stringified later)
auth_obj = {
    "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
    "accessToken": "Atna|EwMDIN31zRahEXqkxAeTd7tr1gNKomvuLeO9wHZeN4vJZuHmEp-oIGvccv-PJDwKiSwFI22pycxrZM55jZVUOchMtTiQVjc-jsH_fB0Qwug2B3Y-JX-Rl9RKhwfsOtfuv7PwuyHC2lfKjr08m3Hjgo7pngJQtabGsURUKzaNXkWSoK_bdMG6C5X1SMFKxpiyDgy573WLfBdSo9--8rVcQ9Fi5yk-kHKeXE8OfnEW3rs8NWYqLizRYA6M5J_7Geq19TwEDWOCatin5ots6heTLmS9CANYKc9VCcw7cBniKoUcDYxqUn7GO3H4GTcNKQxWN3aSBDjjSsPQQBDIrnZCKdkuJi2rAfF44e6QXzufAnd08amUg1xBl9AmiZfHHtbJSH9yoMVXp2uwcpH8-8PR4Fu9hTTL1Xd-pw26HB0Xe6X_rePr7A"
}

# 2️⃣ CSRF JSON (must be stringified later)
csrf_obj = {
    "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
    "token": "3hSCW/VS7AX69orkvVUHwuqZyYN4B54qusiqBzrL6ns=",
    "timestamp": "1772434070",
    "rndNonce": "351867354"
}

# 3️⃣ Build headers object (values that are JSON must be dumped)
headers_obj = {
    "x-amzn-authentication": json.dumps(auth_obj),
    "x-amzn-device-model": "WEBPLAYER",
    "x-amzn-device-width": "1920",
    "x-amzn-device-family": "WebPlayer",
    "x-amzn-device-id": "35793345365583055",
    "x-amzn-user-agent": "Mozilla/5.0",
    "x-amzn-session-id": "357-9334536-5583055",
    "x-amzn-device-height": "1080",
    "x-amzn-request-id": "3653260f-89d6-4310-a62e-f3041da1028f",
    "x-amzn-device-language": "en_US",
    "x-amzn-currency-of-preference": "JPY",
    "x-amzn-os-version": "1.0",
    "x-amzn-application-version": "1.0.9527.0",
    "x-amzn-device-time-zone": "America/New_York",
    "x-amzn-timestamp": "1772431224801",
    "x-amzn-csrf": json.dumps(csrf_obj),
    "x-amzn-music-domain": "music.amazon.co.jp",
    "x-amzn-feature-flags": "hd-supported,uhd-supported",
    "x-amzn-has-profile-id": "true",
    "x-amzn-age-band": ""
}

# 4️⃣ Final payload
payload = {
    "durationSeconds": "265",
    "id": "B07JZ7PW6F",
    "isLibrary": "false",
    "userHash": json.dumps({"level": "HD_MEMBER"}),  # must be string
    "headers": json.dumps(headers_obj)  # entire headers object must be string
}

# 5️⃣ Send request properly
response = requests.post(
    url,
    headers=headers,
    json=payload   # use json= not data=
)

print(response.status_code)
print(response.text)

