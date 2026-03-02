import json
import requests

url = "https://music.amazon.co.jp/embed/B0BQ71QYD6"

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "text/plain;charset=UTF-8",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
}


deeplink_obj = {
    "interface": "DeeplinkInterface.v1_0.DeeplinkClientInformation",
    "deeplink": "/albums/B0BQ6X67MS"
}

authentication_obj = {
    "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
    "accessToken": "Atna|EwMDIKJwXNqIKRSgMxUudZnjcKDrV0loWxNO6wnM-3ibabJQ_OWfsZaC14YudznbIDKD60vckZg8GAMPdgHcBfiTwjiIakImRhNeZ5JyDtJZupa_2jfZisVtFid_KfXTG7Ed9NTgnzP6FDmgh3wunOiiZ9uHUb0_QOCoHMThKHSpBgXGXXyRU3CC3hLFlwYH6jCGtd7UINPWsFcXJ7_sqp0rQjlJzwNsJ8RaTJRCnnhtbetTYuygW0XALlIab1KeE2zU-zCu2DqA7SR4Uj_C_mEPKgkifgGPDYSwId_I8jM0A3Z0c8-xG96EMxtOTymIgudXeJtWzMog7E14NVwGFXu0ORTF_TUk_dxKigdKssXbMilP9V-J4RYh-AeVYFesZu1UKvVzo8tJlxTTR_DOclJFPEzzSxbjHxe6u3A6120vx4uXPLITeurW5gh8qKgXyS3ppgQ"
}

csrf_obj = {
    "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
    "token": "LKH9oof4Gnb+6JYCzUUTI5bNzZC9M5nN7jE/pYGk/gk=",
    "timestamp": "1772419148",
    "rndNonce": "1901901484"
}

headers_obj = {
    "x-amzn-authentication": json.dumps({
        "interface":"ClientAuthenticationInterface.v1_0.ClientTokenElement",
        "accessToken": "Atna|EwMDIBiBYs9B3Wl_UJVXvnZXbhZrymAzxw9BEfMjPPGgRU3dF2TpeGUgrlM9lAMxatY-OqIEXms8FBAWox8IhoXPHuFEbl2QcM8HK8oEtGx0-bT7Y7vOSYnkbEsU2II1gBIAeGKNpjfiaH8uQmjVqVUgt7aIsHp4haCpZFWQvty5lJPx-6XYG51SHsN14RDxYdbLStLLqUnj_b_1MZJ3qAL3l0tQe6St6Mu3X-r300YTTM4y4JlhGq-KYRrpgPMxHKYuv6FbW--kyXKRcjGz3qr0foK-hNKA1i-sgFPTlV1I6s9fLv2Jg24-e2ZRqWL6q6gw2rXfviJjTuQo1g-iSdAHi4Z6rtkB_U4PiGS2G727C73IlYAqgcZVXMgWV0LFgJG6cekBtxoAG7tRnxTSD85KpJfguPEL7kJhJPaSUUt1bXhpMg"
    }),
    "x-amzn-device-model": "WEBPLAYER",
    "x-amzn-device-width": "1920",
    "x-amzn-device-family": "WebPlayer",
    "x-amzn-device-id": "35793345365583055",
    "x-amzn-user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "x-amzn-session-id": "357-9334536-5583055",
    "x-amzn-device-height": "1080",
    "x-amzn-request-id": "2c082208-441a-431b-8983-7eeaf7143a11",
    "x-amzn-device-language": "en_US",
    "x-amzn-currency-of-preference": "JPY",
    "x-amzn-os-version": "1.0",
    "x-amzn-application-version": "1.0.9527.0",
    "x-amzn-device-time-zone": "America/New_York",
    "x-amzn-timestamp": "1772419149227",
    "x-amzn-csrf": json.dumps({
        "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
        "token": "LKH9oof4Gnb+6JYCzUUTI5bNzZC9M5nN7jE/pYGk/gk=",
        "timestamp": "1772419148",
        "rndNonce": "1901901484"
    }),
    "x-amzn-music-domain": "music.amazon.co.jp",
    "x-amzn-referer": "",
    "x-amzn-affiliate-tags": "",
    "x-amzn-ref-marker": "dm_sh_lsTk3BhcFKjVwRYoJgsPoTG1Y",
    "x-amzn-page-url": "https://music.amazon.co.jp/albums/B0BQ6X67MS?marketplaceId=A1VC38T7YXB528&musicTerritory=JP&ref=dm_sh_cRsy07bhxIFU5uglM182qQSy9",
    "x-amzn-weblab-id-overrides": "",
    "x-amzn-video-player-token": "",
    "x-amzn-feature-flags": "hd-supported,uhd-supported",
    "x-amzn-has-profile-id": "true",
    "x-amzn-age-band": ""
}

payload = {
    "deeplink": json.dumps(deeplink_obj),
    "headers": json.dumps(headers_obj)
}


response = requests.post(
    url,
    headers=headers,
)

print("payload:", payload)

with open("response.txt", "w", encoding="utf-8") as f:
    f.write(response.text)

print("Response saved to response.txt")