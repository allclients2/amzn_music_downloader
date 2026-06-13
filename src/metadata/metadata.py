"""Track, album, artist & playlist metadata via the submodule API.

Resolves an ASIN to a `TrackMetadata` / `AlbumMetadata` / `ArtistMetadata` /
`PlaylistMetadata` dataclass. Tracks and albums come from the signed
`AmazonMusicMobileAPI.get_metadata()` (`muse`) endpoint, which supplies disc/track
numbers, duration, ISRC, explicit flag, popularity, composers, and the album tags
(release date, copyright, label, genre). Full-resolution cover art is resolved
separately through `textsearch` (`artOriginal.artUrl`).

Artists and playlists are resolved purely through submodule endpoints (no catalog
search): an artist's discography is harvested from its `get_page("artist/<asin>")`
catalog page, a catalog playlist's members from `get_catalog_playlist`, and a
user/library playlist's members from `get_user_playlist` (a 10-char id is a catalog
ASIN, a longer id — uuid / library id — is a user playlist).
"""

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


def _search_cover(
    session: "AmazonMusicMobileAPI",
    artist: str,
    title: str,
    asins: List[str],
    fallback: Optional[str],
) -> Optional[str]:
    """Original full-resolution cover via the search API (`artOriginal.artUrl`).

    The muse `image` field is only a 600px asset. The textsearch service exposes
    `artOriginal.artUrl` — the original master art (often 1500-3000px+) — which is
    what OrpheusDL embeds. Best effort: any failure (or no usable asin) falls back
    to the supplied `fallback` (the muse image, size token rewritten upward).
    """
    asins = [a for a in asins if a]
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


def _hi_res_cover(session: "AmazonMusicMobileAPI", album_data: dict) -> Optional[str]:
    """Hi-res cover for an album (muse dict) — the shared per-album lookup used by
    the album branch of `fetch_metadata`."""
    return _search_cover(
        session,
        artist=(
            album_data.get("primaryArtistName")
            or (album_data.get("artist") or {}).get("name")
            or ""
        ),
        title=album_data.get("title") or "",
        asins=[
            album_data.get("asin"),
            album_data.get("globalAsin"),
            album_data.get("requestedAsin"),
            *(t.get("asin") for t in album_data.get("tracks", [])),
        ],
        fallback=_upgrade_cover(album_data.get("image")),
    )


def resolve_track_cover(session: "AmazonMusicMobileAPI", track: "TrackMetadata") -> Optional[str]:
    """Hi-res cover for a single track, keyed off its `TrackMetadata` fields.

    The single-track fast path defers the cover lookup out of `fetch_metadata`
    (which returns the muse fallback in `track.cover_url`) so this textsearch can
    run concurrently with the license + audio download instead of blocking them.
    Falls back to the muse image already on the track.
    """
    return _search_cover(
        session,
        artist=track.album_artist or track.artist or "",
        title=track.album_name or "",
        asins=[track.album_asin, track.asin],
        fallback=track.cover_url,
    )


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


def _resolve_playlist(
    session: AmazonMusicMobileAPI, content_asin: str, prefer_user: bool
) -> Optional["PlaylistMetadata"]:
    """Resolve a playlist id to `PlaylistMetadata`, trying the preferred endpoint
    (catalog vs user/library) first and the other as fallback; None if neither serves
    it. An id's catalog-vs-user nature is only a heuristic (length) or a link-shape
    hint, so one of the two calls may be wasted — `prefer_user` orders them to make
    the wasted one the less likely."""
    # Imported lazily to avoid an import cycle (playlist imports this module).
    from metadata.playlist import try_fetch_playlist, try_fetch_user_playlist
    if prefer_user:
        return (try_fetch_user_playlist(session, content_asin)
                or try_fetch_playlist(session, content_asin))
    return (try_fetch_playlist(session, content_asin)
            or try_fetch_user_playlist(session, content_asin))


def fetch_metadata(
    session: AmazonMusicMobileAPI, content_asin: str, defer_track_cover: bool = False,
    type_hint: Optional[str] = None,
) -> Tuple[str, object]:
    """Resolve an ASIN to its kind and metadata:

    ('track', TrackMetadata), ('album', AlbumMetadata),
    ('artist', ArtistMetadata), or ('playlist', PlaylistMetadata).

    With `defer_track_cover=True` a single *track* skips the inline hi-res cover
    textsearch (leaving the muse fallback in `cover_url`); the single-track fast
    path resolves it concurrently via `resolve_track_cover` so it doesn't block
    the license. Albums/artists/playlists are unaffected (their cover lookup is a
    shared per-album call, not on the per-track critical path).

    `type_hint` ('playlist' / 'user-playlist', from a link's shape) lets a known
    playlist skip the doomed muse `get_metadata` call (playlists aren't served by
    muse) and hit the named catalog vs user/library endpoint first. A wrong hint is
    harmless: if the playlist endpoints don't resolve it, we fall through to the full
    muse-based resolution below.
    """
    # A link of known playlist shape is served only by the playlist endpoints, never
    # muse — skip the wasted muse round-trip and resolve it directly, trying the
    # endpoint the link shape names (catalog vs user/library) first.
    if type_hint in ("playlist", "user-playlist"):
        playlist = _resolve_playlist(
            session, content_asin, prefer_user=(type_hint == "user-playlist")
        )
        if playlist is not None:
            return "playlist", playlist

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
        # Imported lazily to avoid an import cycle (discography imports this module).
        from metadata.discography import build_artist
        return "artist", build_artist(session, artist_match or artists_list[0], content_asin)

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
        # Defer the hi-res textsearch to the caller (fast path) when asked, so it
        # overlaps the download instead of blocking it; else resolve it inline.
        cover_url = (
            _upgrade_cover(album_data.get("image"))
            if defer_track_cover
            else _hi_res_cover(session, album_data)
        )
        return "track", _build_track(
            track_data, album_data, _disc_total(album_data), cover_url
        )

    # ── Full album ────────────────────────────────────────────────────────
    album_data = album_match or (albums_list[0] if albums_list else None)
    if not album_data:
        # Not a track/album/artist — the only remaining content the pipeline can
        # resolve is a playlist, served by a different endpoint. A 10-char id is a
        # catalog-playlist ASIN; a longer id (uuid / library id) is a user playlist
        # (mirrors the submodule's `len == 10` discriminator). Try the likely
        # endpoint first, then the other, so either link shape resolves.
        playlist = _resolve_playlist(
            session, content_asin, prefer_user=len(str(content_asin)) != 10
        )
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
