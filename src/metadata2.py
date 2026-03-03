
import json
import uuid
import requests
import time
from metadata_objects import AlbumMetadata

class Metadata2:
    BASE_URL = "https://fe.web.skill.music.a2z.com/api"

    @staticmethod
    def _build_common_headers(track_asin: str, config: dict):
        base_headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "https://music.amazon.co.jp",
            "Referer": "https://music.amazon.co.jp/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        auth_obj = {
            "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
            "accessToken": config["accessToken"]
        }

        csrf_obj = {
            "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
            "token": config["csrf"]["token"],
            "timestamp": config["csrf"]["ts"],
            "rndNonce": config["csrf"]["rnd"]
        }

        headers_obj = {
            "x-amzn-authentication": json.dumps(auth_obj),
            "x-amzn-device-model": "WEBPLAYER",
            "x-amzn-device-width": "1920",
            "x-amzn-device-family": "WebPlayer",
            "x-amzn-device-id": config["deviceId"],
            "x-amzn-user-agent": "Mozilla/5.0",
            "x-amzn-session-id": config.get("sessionId", ""),
            "x-amzn-device-height": "1080",
            "x-amzn-request-id": str(uuid.uuid4()),
            "x-amzn-device-language": "en_US",
            "x-amzn-currency-of-preference": config.get("currency", "JPY"),
            "x-amzn-os-version": "1.0",
            "x-amzn-application-version": "1.0.9527.0",
            "x-amzn-device-time-zone": config.get("timezone", "America/New_York"),
            "x-amzn-timestamp": str(int(time.time() * 1000)),
            "x-amzn-csrf": json.dumps(csrf_obj),
            "x-amzn-music-domain": config.get("musicDomain", "music.amazon.co.jp"),
            "x-amzn-feature-flags": "hd-supported,uhd-supported",
            "x-amzn-has-profile-id": "true",
            "x-amzn-age-band": "",
            "x-amzn-referer": "music.amazon.co.jp",
            "x-amzn-affiliate-tags": "",
            "x-amzn-ref-marker": "",
            "x-amzn-page-url": f"https://music.amazon.co.jp/albums/{track_asin}",
            "x-amzn-weblab-id-overrides": "",
        }

        return base_headers, headers_obj

    @staticmethod
    def _post(endpoint: str, track_asin: str, config: dict, extra_payload: dict = None):
        base_headers, headers_obj = Metadata2._build_common_headers(track_asin, config)

        payload = {
            "id": track_asin,
            "userHash": json.dumps({"level": "HD_MEMBER"}),
            "headers": json.dumps(headers_obj)
        }

        if extra_payload:
            payload.update(extra_payload)

        response = requests.post(
            f"{Metadata2.BASE_URL}/{endpoint}",
            headers=base_headers,
            json=payload
        )

        return response.json()
    
    @staticmethod
    def get_album_metadata(album_asin: str, config: dict) -> AlbumMetadata:
        try:
            base_headers, headers_obj = Metadata2._build_common_headers(album_asin, config)

            deeplink_obj = {
                "interface": "DeeplinkInterface.v1_0.DeeplinkClientInformation",
                "deeplink": f"/albums/{album_asin}"
            }

            payload = {
                "deeplink": json.dumps(deeplink_obj),
                "headers": json.dumps(headers_obj)
            }

            response = requests.post(
                f"{Metadata2.BASE_URL}/showHome",
                headers=base_headers,
                json=payload,
                timeout=10
            )

            response.raise_for_status()

            return AlbumMetadata.from_response(album_asin, response.text)

        except requests.RequestException as e:
            raise RuntimeError(f"network error fetching album {album_asin}") from e
        except Exception as e:
            raise RuntimeError(f"failed parsing album metadata for {album_asin}") from e
            
    @staticmethod
    def fetch_artwork_v2(track_asin: str, config: dict):
        data = Metadata2._post(
            endpoint="playCatalogAlbum",
            track_asin=track_asin,
            config=config
        )

        for method in data.get("methods", []):
            if method.get("interface") == "PlaybackInterface.v1_0.SetMediaMethod":
                return method.get("metadata", {}).get("artwork")

        return None
    
    @staticmethod
    def fetch_lyrics(track_asin: str, duration: int, config: dict):
        data = Metadata2._post(
            endpoint="showLyrics",
            track_asin=track_asin,
            config=config,
            extra_payload={
                "durationSeconds": str(duration),
                "isLibrary": "false",
            }
        )

        return data