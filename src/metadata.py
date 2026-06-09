"""Metadata via OrpheusDL's `muse` API (replaces the old embed-page scraper).

The previous implementation scraped `music.amazon.co.jp/embed/{asin}` with
BeautifulSoup and was the only source of disc/track numbers. The vendored
`AmazonMusicMobileAPI.get_metadata()` (the `muse` endpoint) returns disc/track
numbers plus richer tags (release date, copyright, label, genre, ISRC, explicit,
popularity, composers) over a signed API call — no scraping, fully multi-region.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from vendor.amazonmusic.azapi import AmazonMusicMobileAPI

# muse accepts batched ASIN lookups; chunk album track queries to stay well
# within request limits.
_BATCH_SIZE = 20


@dataclass
class TrackMetadata:
    """Everything the FLAC tagger + folder builder needs for a single track."""
    asin: str
    title: str
    artist: str
    album_name: str
    album_artist: str
    album_asin: str
    disc: int
    track_number: int
    total_tracks: int
    total_discs: int
    duration_seconds: int
    is_explicit: bool
    isrc: Optional[str] = None
    popularity: Optional[int] = None
    composers: Optional[str] = None
    release_date: Optional[str] = None  # YYYY-MM-DD
    copyright: Optional[str] = None
    label: Optional[str] = None
    genre: Optional[str] = None
    cover_url: Optional[str] = None


@dataclass
class AlbumMetadata:
    album_name: str
    artist_name: str
    album_asin: str
    cover_url: Optional[str]
    release_date: Optional[str]
    copyright: Optional[str]
    label: Optional[str]
    genre: Optional[str]
    track_count: int
    total_discs: int
    tracks: List[TrackMetadata] = field(default_factory=list)


def _ms_to_date(ms) -> Optional[str]:
    """Epoch-milliseconds -> 'YYYY-MM-DD' (UTC), or None."""
    if ms in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _upgrade_cover(url: Optional[str]) -> Optional[str]:
    """Bump an Amazon image URL to a larger size (best effort)."""
    if not url:
        return url
    # Replace any `._SX600_` / `._SY600_` / `._SL600_` / `._UX...` size token.
    return re.sub(r"\._(S[XYL]|U[XY])\d+_", "._SL1200_", url)


def _composers(track_data: dict) -> Optional[str]:
    writers = [str(w).strip() for w in (track_data.get("songWriters") or []) if w]
    if not writers:
        return None
    # Dedupe while keeping it deterministic.
    return "; ".join(sorted(set(writers)))


def _album_release_date(album_data: dict) -> Optional[str]:
    return _ms_to_date(
        album_data.get("originalReleaseDate") or album_data.get("merchantReleaseDate")
    )


def _build_track(track_data: dict, album_data: dict, disc_total: int) -> TrackMetadata:
    product = album_data.get("productDetails") or {}
    return TrackMetadata(
        asin=track_data.get("asin"),
        title=track_data.get("title"),
        artist=(track_data.get("artist") or {}).get("name"),
        album_name=album_data.get("title"),
        album_artist=album_data.get("primaryArtistName")
        or (album_data.get("artist") or {}).get("name"),
        album_asin=album_data.get("asin"),
        disc=int(track_data.get("discNum") or 1),
        track_number=int(track_data.get("trackNum") or 1),
        total_tracks=int(album_data.get("trackCount") or 1),
        total_discs=disc_total,
        duration_seconds=int(track_data.get("duration") or 0),  # muse gives seconds
        is_explicit=bool(
            (track_data.get("parentalControls") or {}).get("hasExplicitLanguage", False)
        ),
        isrc=track_data.get("isrc"),
        popularity=track_data.get("popularity"),
        composers=_composers(track_data),
        release_date=_album_release_date(album_data),
        copyright=product.get("copyright"),
        label=product.get("label"),
        genre=product.get("primaryGenreName"),
        cover_url=_upgrade_cover(album_data.get("image")),
    )


def _disc_total(album_data: dict) -> int:
    discs = [int(t.get("discNum") or 1) for t in album_data.get("tracks", []) if t]
    return max(discs) if discs else 1


def _fetch_album_data(session: AmazonMusicMobileAPI, album_asin: str) -> dict:
    resp = session.get_metadata((album_asin,))
    albums = resp.get("albumList") or []
    if not albums:
        raise ValueError(f"album {album_asin} not found in muse response")
    return albums[0]


def _fetch_tracks(session: AmazonMusicMobileAPI, asins: List[str]) -> dict:
    """Return a {asin: track_data} map for the given track ASINs (batched)."""
    out: dict = {}
    for i in range(0, len(asins), _BATCH_SIZE):
        chunk = tuple(asins[i:i + _BATCH_SIZE])
        resp = session.get_metadata(chunk)
        for td in resp.get("trackList") or []:
            if td.get("asin"):
                out[td["asin"]] = td
    return out


def fetch_metadata(
    session: AmazonMusicMobileAPI, content_asin: str
) -> Tuple[str, object]:
    """Resolve an ASIN to ('track', TrackMetadata) or ('album', AlbumMetadata)."""
    resp = session.get_metadata((content_asin,))
    tracks_list = resp.get("trackList") or []
    albums_list = resp.get("albumList") or []

    track_data = next((t for t in tracks_list if t.get("asin") == content_asin), None)
    album_match = next((a for a in albums_list if a.get("asin") == content_asin), None)

    # ── Single track ──────────────────────────────────────────────────────
    if track_data or (tracks_list and not album_match):
        track_data = track_data or tracks_list[0]
        album_asin = (track_data.get("album") or {}).get("asin")
        if not album_asin:
            raise ValueError(f"track {content_asin} has no album reference")
        # A track lookup already returns its album in the same response — reuse
        # it instead of making a second muse call.
        album_data = next((a for a in albums_list if a.get("asin") == album_asin), None)
        if not album_data:
            album_data = _fetch_album_data(session, album_asin)
        return "track", _build_track(track_data, album_data, _disc_total(album_data))

    # ── Full album ────────────────────────────────────────────────────────
    album_data = album_match or (albums_list[0] if albums_list else None)
    if not album_data:
        raise ValueError(f"ASIN {content_asin} did not resolve to a track or album")

    disc_total = _disc_total(album_data)
    track_count = int(album_data.get("trackCount") or 0)
    light_tracks = album_data.get("tracks") or []
    track_asins = [t.get("asin") for t in light_tracks if t.get("asin")]
    rich = _fetch_tracks(session, track_asins)

    tracks: List[TrackMetadata] = []
    for lt in light_tracks:
        asin = lt.get("asin")
        # Prefer the rich per-track record; fall back to the album's lightweight one.
        td = rich.get(asin, lt)
        tracks.append(_build_track(td, album_data, disc_total))

    product = album_data.get("productDetails") or {}
    album = AlbumMetadata(
        album_name=album_data.get("title"),
        artist_name=album_data.get("primaryArtistName")
        or (album_data.get("artist") or {}).get("name"),
        album_asin=album_data.get("asin"),
        cover_url=_upgrade_cover(album_data.get("image")),
        release_date=_album_release_date(album_data),
        copyright=product.get("copyright"),
        label=product.get("label"),
        genre=product.get("primaryGenreName"),
        track_count=track_count or len(tracks),
        total_discs=disc_total,
        tracks=tracks,
    )
    return "album", album
