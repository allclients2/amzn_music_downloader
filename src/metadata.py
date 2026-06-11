"""Track, album, artist & playlist metadata via the submodule API.

Resolves an ASIN to a `TrackMetadata` / `AlbumMetadata` / `ArtistMetadata` /
`PlaylistMetadata` dataclass. Tracks and albums come from the signed
`AmazonMusicMobileAPI.get_metadata()` (`muse`) endpoint, which supplies disc/track
numbers, duration, ISRC, explicit flag, popularity, composers, and the album tags
(release date, copyright, label, genre). Full-resolution cover art is resolved
separately through `textsearch` (`artOriginal.artUrl`).

Artists and playlists are resolved purely through submodule endpoints (no catalog
search): an artist's discography is harvested from its `get_page("artist/<asin>")`
catalog page, and a playlist's members from `get_catalog_playlist`.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from amazonmusic.azapi import AmazonMusicMobileAPI

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


@dataclass
class PlaylistMetadata:
    """A playlist resolved to its member tracks. Each `TrackMetadata` keeps its own
    album/artist tags, so tracks still land under `<album_artist>/<album>/`; `name`
    is only used for the progress header."""
    name: str
    asin: str
    tracks: List[TrackMetadata] = field(default_factory=list)


@dataclass
class ArtistMetadata:
    """An artist resolved to the ASINs of every album in its discography. The album
    pipeline is then run per ASIN, so no per-track data is carried here."""
    name: str
    asin: str
    album_asins: List[str] = field(default_factory=list)


def _ms_to_date(ms) -> Optional[str]:
    """Epoch-milliseconds -> 'YYYY-MM-DD' (UTC), or None."""
    if ms in (None, "", 0):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _upgrade_cover(url: Optional[str]) -> Optional[str]:
    """Bump an Amazon image URL to a larger size (best effort).

    Only used as a fallback for the muse `image` asset (a 600px render). The
    preferred path is `_hi_res_cover`, which pulls the original master art.
    """
    if not url:
        return url
    # Replace any `._SX600_` / `._SY600_` / `._SL600_` / `._UX...` size token.
    return re.sub(r"\._(S[XYL]|U[XY])\d+_", "._SL1200_", url)


def _hi_res_cover(session: "AmazonMusicMobileAPI", album_data: dict) -> Optional[str]:
    """Original full-resolution cover via the search API (`artOriginal.artUrl`).

    The muse `image` field is only a 600px asset. The textsearch service exposes
    `artOriginal.artUrl` — the original master art (often 1500-3000px+) — which is
    what OrpheusDL embeds. Best effort: any failure falls back to the muse image
    with its size token rewritten upward.
    """
    fallback = _upgrade_cover(album_data.get("image"))

    artist = (
        album_data.get("primaryArtistName")
        or (album_data.get("artist") or {}).get("name")
        or ""
    )
    title = album_data.get("title") or ""
    asins = [
        a
        for a in (
            album_data.get("asin"),
            album_data.get("globalAsin"),
            album_data.get("requestedAsin"),
        )
        if a
    ]
    asins += [t.get("asin") for t in album_data.get("tracks", []) if t.get("asin")]
    if not asins:
        return fallback

    try:
        doc = session.search(
            query=f'"{artist}" - "{title}"',
            asins=tuple(asins),
            search_types=("catalog_album", "catalog_track"),
            limit=100,
            region_to_use=session.credentials.account_region,
        )
    except Exception:
        return fallback

    if not isinstance(doc, dict):
        return fallback

    art = doc.get("artOriginal") or {}
    url = art.get("artUrl") if isinstance(art, dict) else None
    if not url:
        album_art = (doc.get("metadata") or {}).get("albumArt") or {}
        url = album_art.get("url") if isinstance(album_art, dict) else None
    # `artOriginal.artUrl` is a bare master URL (no size token) = original
    # resolution, so we return it as-is.
    return str(url) if url else fallback


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


def _build_track(
    track_data: dict, album_data: dict, disc_total: int, cover_url: Optional[str]
) -> TrackMetadata:
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
        cover_url=cover_url,
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
    """Resolve an ASIN to its kind and metadata:

    ('track', TrackMetadata), ('album', AlbumMetadata),
    ('artist', ArtistMetadata), or ('playlist', PlaylistMetadata).
    """
    # Playlists aren't served by the muse lookup endpoint, so a playlist ASIN can
    # make get_metadata error — swallow that and fall through to the playlist path.
    try:
        resp = session.get_metadata((content_asin,))
    except Exception:
        resp = {}
    tracks_list = resp.get("trackList") or []
    albums_list = resp.get("albumList") or []
    artists_list = resp.get("artistList") or []

    track_data = next((t for t in tracks_list if t.get("asin") == content_asin), None)
    album_match = next((a for a in albums_list if a.get("asin") == content_asin), None)
    artist_match = next((a for a in artists_list if a.get("asin") == content_asin), None)

    # ── Artist ────────────────────────────────────────────────────────────
    # An artist lookup can also surface the artist's popular tracks/top albums, so
    # only an exact artistList hit (or a response carrying *nothing but* artists)
    # routes here — otherwise the track/album fallbacks below stay in charge.
    if artist_match or (artists_list and not albums_list and not tracks_list):
        return "artist", _build_artist(session, artist_match or artists_list[0], content_asin)

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
        cover_url = _hi_res_cover(session, album_data)
        return "track", _build_track(
            track_data, album_data, _disc_total(album_data), cover_url
        )

    # ── Full album ────────────────────────────────────────────────────────
    album_data = album_match or (albums_list[0] if albums_list else None)
    if not album_data:
        # Not a track/album/artist — the only remaining content the pipeline can
        # resolve is a (catalog) playlist, served by a different endpoint.
        playlist = _try_fetch_playlist(session, content_asin)
        if playlist is not None:
            return "playlist", playlist
        raise ValueError(
            f"ASIN {content_asin} did not resolve to a track, album, artist, or playlist"
        )

    disc_total = _disc_total(album_data)
    track_count = int(album_data.get("trackCount") or 0)
    light_tracks = album_data.get("tracks") or []
    track_asins = [t.get("asin") for t in light_tracks if t.get("asin")]
    rich = _fetch_tracks(session, track_asins)

    # One search call for the whole album; every track shares the same cover.
    cover_url = _hi_res_cover(session, album_data)

    tracks: List[TrackMetadata] = []
    for lt in light_tracks:
        asin = lt.get("asin")
        # Prefer the rich per-track record; fall back to the album's lightweight one.
        td = rich.get(asin, lt)
        tracks.append(_build_track(td, album_data, disc_total, cover_url))

    product = album_data.get("productDetails") or {}
    album = AlbumMetadata(
        album_name=album_data.get("title"),
        artist_name=album_data.get("primaryArtistName")
        or (album_data.get("artist") or {}).get("name"),
        album_asin=album_data.get("asin"),
        cover_url=cover_url,
        release_date=_album_release_date(album_data),
        copyright=product.get("copyright"),
        label=product.get("label"),
        genre=product.get("primaryGenreName"),
        track_count=track_count or len(tracks),
        total_discs=disc_total,
        tracks=tracks,
    )
    return "album", album


# ── Artist ──────────────────────────────────────────────────────────────────
# Album ASINs are discovered from the artist's catalog pages (`get_page`), never
# from textsearch. The page payload models each album as a dict tagged with a
# `__type` of `...brush#Album`, whose `asin` is the album ASIN.
_ALBUM_TYPE_SUFFIX = "#Album"
_ARTIST_PAGE_MAX_PAGES = 40   # hard stop on discography pagination


def _find_next_token(obj) -> Optional[str]:
    """The pagination `nextToken` anywhere in a get_page payload, or None."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, value in cur.items():
                lk = str(key).lower()
                if "next" in lk and "token" in lk and value:
                    return str(value)
                if isinstance(value, (dict, list, tuple)):
                    stack.append(value)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return None


def _collect_album_asins(obj, seen: set, ordered: List[str]) -> None:
    """Append (in encounter order, de-duped) the ASIN of every album entity — a
    dict whose `__type` ends in `#Album` — found anywhere in a get_page payload."""
    if isinstance(obj, dict):
        if str(obj.get("__type", "")).endswith(_ALBUM_TYPE_SUFFIX) and obj.get("asin"):
            asin = str(obj["asin"])
            if asin not in seen:
                seen.add(asin)
                ordered.append(asin)
        for value in obj.values():
            if isinstance(value, (dict, list, tuple)):
                _collect_album_asins(value, seen, ordered)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_album_asins(item, seen, ordered)


def _artist_album_asins(session: AmazonMusicMobileAPI, artist_asin: str) -> List[str]:
    """Every album ASIN in an artist's discography, de-duped, with no catalog search.

    The artist landing page (`artist/<asin>`) surfaces the popular/new-release
    albums up front, then the `chronological-albums` shoveler is paginated (via its
    `nextToken`) for the complete discography.
    """
    seen: set = set()
    ordered: List[str] = []

    try:
        _collect_album_asins(session.get_page(f"artist/{artist_asin}"), seen, ordered)
    except Exception:
        pass

    next_token = None
    for _ in range(_ARTIST_PAGE_MAX_PAGES):
        try:
            page = session.get_page(
                f"artist/{artist_asin}/chronological-albums", next_token=next_token
            )
        except Exception:
            break
        _collect_album_asins(page, seen, ordered)
        next_token = _find_next_token(page)
        if not next_token:
            break
    return ordered


def _build_artist(
    session: AmazonMusicMobileAPI, artist_data: dict, content_asin: str
) -> ArtistMetadata:
    artist_asin = str(artist_data.get("asin") or content_asin)
    return ArtistMetadata(
        name=artist_data.get("name") or "Unknown Artist",
        asin=artist_asin,
        album_asins=_artist_album_asins(session, artist_asin),
    )


# ── Playlist ────────────────────────────────────────────────────────────────
def _playlist_track_asin(track: dict) -> Optional[str]:
    """The track ASIN from a playlist member entry (nested under `metadata` or flat)."""
    if not isinstance(track, dict):
        return None
    meta = track.get("metadata")
    if isinstance(meta, dict) and meta.get("asin"):
        return str(meta["asin"])
    if track.get("asin"):
        return str(track["asin"])
    return None


def _build_tracks_from_asins(
    session: AmazonMusicMobileAPI, track_asins: List[str]
) -> List[TrackMetadata]:
    """`TrackMetadata` for an arbitrary list of track ASINs (playlist members).

    The muse lookups are batched, and each distinct album is fetched (and its cover
    resolved) only once, while the original track order is preserved.
    """
    rich = _fetch_tracks(session, track_asins)
    album_cache: dict = {}
    cover_cache: dict = {}
    tracks: List[TrackMetadata] = []
    for asin in track_asins:
        td = rich.get(asin)
        if not td:
            continue
        album_asin = (td.get("album") or {}).get("asin")
        if not album_asin:
            continue
        if album_asin not in album_cache:
            album_data = _fetch_album_data(session, album_asin)
            album_cache[album_asin] = album_data
            cover_cache[album_asin] = _hi_res_cover(session, album_data)
        album_data = album_cache[album_asin]
        tracks.append(
            _build_track(td, album_data, _disc_total(album_data), cover_cache[album_asin])
        )
    return tracks


def _try_fetch_playlist(
    session: AmazonMusicMobileAPI, playlist_asin: str
) -> Optional[PlaylistMetadata]:
    """Resolve a catalog-playlist ASIN to a `PlaylistMetadata`, or None if the ASIN
    isn't a playlist (so the caller can raise a single combined error)."""
    try:
        catalog = session.get_catalog_playlist(playlist_asin)
    except Exception:
        return None
    if not isinstance(catalog, dict):
        return None
    p_data = catalog.get("playlist")
    if not isinstance(p_data, dict):
        p_data = catalog
    raw_tracks = p_data.get("tracks") or []
    track_asins = [a for a in (_playlist_track_asin(t) for t in raw_tracks) if a]
    if not track_asins:
        return None
    tracks = _build_tracks_from_asins(session, track_asins)
    if not tracks:
        return None
    meta = p_data.get("metadata") if isinstance(p_data.get("metadata"), dict) else {}
    name = (
        meta.get("title")
        or p_data.get("title")
        or p_data.get("name")
        or "Unknown Playlist"
    )
    return PlaylistMetadata(name=str(name), asin=str(playlist_asin), tracks=tracks)
