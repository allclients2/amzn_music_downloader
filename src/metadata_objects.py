from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone
from lyrics import Lyrics
import json
import re

@dataclass
class TrackMetadata:
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
class AlbumMetadata:
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
    tracks: List[TrackMetadata] = field(default_factory=list)

    @staticmethod
    def from_response(album_asin: str, response_text: str) -> "AlbumMetadata":
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
                    TrackMetadata(
                        asin=track_asin,
                        title=title,
                        artist=album_artist,
                        duration_seconds=duration_seconds,
                        is_explicit=is_explicit,
                        lyrics_available=lyrics_available,
                        popularity=popularity
                    )
                )

        return AlbumMetadata(
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