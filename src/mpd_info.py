# com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getLicenseForPlaybackV2

import requests
import uuid

class MpdInfo:
    def getTrackInfo(contentAsin: str, config, cookieHeader: str) -> str:
        url = "https://music.amazon.co.jp/FE/api/dmls/"

        headers = {
            "Authorization": "Bearer " + config["accessToken"],
            "Cookie": cookieHeader,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "X-Amz-Target": "com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getDashManifestsV2",
            "Csrf-Token": config["csrf"]["token"],
            "Csrf-Rnd": config["csrf"]["rnd"],
            "Csrf-Ts": config["csrf"]["ts"],
            "Content-Encoding": "amz-1.0",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://music.amazon.co.jp"
        }

        payload = {
            "deviceToken": {
                "deviceTypeId": config["deviceType"],
                "deviceId": config["deviceId"]
            },
            "appMetadata": {
                "https": "true"
            },
            "clientMetadata": {
                "clientId": "WebCP",
                "clientRequestId": str(uuid.uuid4())
            },
            "customerId": config["customerId"],
            "contentIdList": [
                {
                    "identifier": contentAsin, # e.g.: "B0CBVKPZ68"
                    "identifierType": "ASIN"
                }
            ],
            "musicDashVersionList": [
                "SIREN_KATANA"
            ],
            "contentProtectionList": [
                "TRACK_PSSH"
            ],
            "tryAsinSubstitution": "true",
            "customerInfo": {
                "marketplaceId": config["marketplaceId"],
                "territoryId": config["musicTerritory"]
            },
            "appInfo": {
                "musicAgent": "Maestro/1.0 WebCP/1.0.9527.0 (d12c-cb21-WebC-d25b-627dd)"
            }
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload
        )

        return response.json()["contentResponseList"][0]["manifest"]


