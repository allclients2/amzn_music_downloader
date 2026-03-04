
import json
import uuid
import requests
import time
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone
from lyrics import Lyrics
import json
import re

@dataclass
class TrackMetadataV2:
    asin: str
    title: str
    artist: str
    duration_seconds: int
    is_explicit: bool
    lyrics_available: bool
    popularity: Optional[int]
    lyrics: Optional["Lyrics"] = field(default=None)

    def attach_lyrics(self, lyrics: "Lyrics"):
        self.lyrics = lyrics

@dataclass
class AlbumMetadataV2:
    asin: str
    name: str
    artist: str
    total_duration_seconds: int
    release_date_iso: Optional[str]
    copyright: Optional[str]
    label: Optional[str]
    track_count: int
    cover_art_url: Optional[str]
    background_image_url: Optional[str]
    tracks: List[TrackMetadataV2] = field(default_factory=list)

    @staticmethod
    def from_response(album_asin: str, response_text: str) -> "AlbumMetadataV2":
        data = json.loads(response_text)

        # Locate DetailTemplate
        detail_template = None
        for method in data.get("methods", []):
            template = method.get("template")
            if not template:
                continue
            if template.get("interface", "").endswith("DetailTemplate"):
                detail_template = template
                break

        if not detail_template:
            raise ValueError("DetailTemplate not found in response")

        # Album basic metadata
        album_name = detail_template.get("headerText", {}).get("text")
        album_artist = detail_template.get("headerPrimaryText")
        header_tertiary = detail_template.get("headerTertiaryText", "")
        copyright_text = detail_template.get("footer")
        cover_art = detail_template.get("headerImage")
        background_image = detail_template.get("backgroundImage")

        # Parse release date from tertiary header
        # Example:
        # "18 SONGS  •  1 HOUR AND 13 MINUTES  •  MAY 13 2022"
        release_date_iso = None
        track_count = 0
        total_duration_seconds = 0

        if header_tertiary:
            parts = [p.strip() for p in header_tertiary.split("•")]

            # Track count
            if parts:
                match = re.search(r"(\d+)\s+SONG", parts[0], re.IGNORECASE)
                if match:
                    track_count = int(match.group(1))

            # Duration
            if len(parts) > 1:
                total_duration_seconds = _parse_album_duration(parts[1])

            # Release date
            if len(parts) > 2:
                release_date_iso = _parse_release_date(parts[2])

        # Extract tracks
        tracks = []
        widgets = detail_template.get("widgets", [])
        table_widget = None

        for w in widgets:
            if w.get("interface", "").endswith("DescriptiveTableWidgetElement"):
                table_widget = w
                break

        if table_widget:
            for item in table_widget.get("items", []):
                if not item.get("interface", "").endswith("DescriptiveRowItemElement"):
                    continue

                title = item.get("primaryText")

                deeplink = item.get("primaryLink", {}).get("deeplink", "")
                track_asin = _extract_track_asin(deeplink)

                duration_str = item.get("secondaryText3", "")
                duration_seconds = _parse_track_duration(duration_str)

                badges = item.get("badges", []) or []
                secondary_badges = item.get("secondaryBadges", []) or []

                is_explicit = "E" in badges or "E" in item.get("primaryBadges", [])
                lyrics_available = "LYRICS" in badges or "LYRICS" in secondary_badges

                popularity = item.get("popularity")

                tracks.append(
                    TrackMetadataV2(
                        asin=track_asin,
                        title=title,
                        artist=album_artist,
                        duration_seconds=duration_seconds,
                        is_explicit=is_explicit,
                        lyrics_available=lyrics_available,
                        popularity=popularity
                    )
                )

        return AlbumMetadataV2(
            asin=album_asin,
            name=album_name,
            artist=album_artist,
            total_duration_seconds=total_duration_seconds,
            release_date_iso=release_date_iso,
            copyright=copyright_text,
            label=_extract_label_from_copyright(copyright_text),
            track_count=track_count,
            cover_art_url=cover_art,
            background_image_url=background_image,
            tracks=tracks
        )


def _extract_track_asin(deeplink: str) -> Optional[str]:
    match = re.search(r"trackAsin=([A-Z0-9]+)", deeplink)
    return match.group(1) if match else None


def _parse_track_duration(duration_str: str) -> int:
    # "03:16"
    if not duration_str:
        return 0
    parts = duration_str.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + int(seconds)
    return 0


def _parse_album_duration(text: str) -> int:
    # "1 HOUR AND 13 MINUTES"
    hours = 0
    minutes = 0

    hour_match = re.search(r"(\d+)\s+HOUR", text, re.IGNORECASE)
    minute_match = re.search(r"(\d+)\s+MINUTE", text, re.IGNORECASE)

    if hour_match:
        hours = int(hour_match.group(1))
    if minute_match:
        minutes = int(minute_match.group(1))

    return hours * 3600 + minutes * 60

def _parse_release_date(text: str) -> Optional[str]:
    if not text:
        return None

    cleaned = text.strip().title()

    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            dt = datetime.strptime(cleaned, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        except ValueError:
            continue

    return None

def _extract_label_from_copyright(copyright_text: Optional[str]) -> Optional[str]:
    if not copyright_text:
        return None
    # "℗© 2022 Aftermath/Interscope Records"
    match = re.search(r"\d{4}\s+(.+)", copyright_text)
    return match.group(1) if match else None


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
    def get_album_metadatav2(album_asin: str, config: dict) -> AlbumMetadataV2:
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

            return AlbumMetadataV2.from_response(album_asin, response.text)

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