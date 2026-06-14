"""Playlist resolution via submodule endpoints (catalog and user/library).

A catalog playlist's members come from `get_catalog_playlist`, a user/library
playlist's from `get_user_playlist` (a 10-char id is a catalog ASIN, a longer id —
uuid / library id — is a user playlist). Both endpoints model a playlist as a dict
with a `tracks` list and a `metadata.title`, so they share one builder. Each member
track is resolved to a full `TrackMetadata` (reusing the `metadata` builders) and
keeps its own album/artist tags, so tracks still land under `<album_artist>/<album>/`.
"""

from typing import List, Optional

from amazonmusic.azapi import AmazonMusicMobileAPI

from metadata.metadata import (
    PlaylistMetadata,
    TrackMetadata,
    _build_track,
    _disc_total,
    _fetch_album_data,
    _hi_res_cover,
    fetch_tracks_and_albums,
)


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

    The muse lookups are batched and each member's album rides along in the *same*
    response (`fetch_tracks_and_albums`), so no second per-album round-trip is made;
    each distinct album's cover is resolved only once and the original track order is
    preserved. (The direct-download path uses the concurrent, progress-reporting
    equivalent in `process.download`; this serial builder backs the batch path.)
    """
    rich, albums = fetch_tracks_and_albums(session, track_asins)
    cover_cache: dict = {}
    tracks: List[TrackMetadata] = []
    for asin in track_asins:
        td = rich.get(asin)
        if not td:
            continue
        album_asin = (td.get("album") or {}).get("asin")
        if not album_asin:
            continue
        album_data = albums.get(album_asin)
        if album_data is None:
            album_data = albums[album_asin] = _fetch_album_data(session, album_asin)
        if album_asin not in cover_cache:
            cover_cache[album_asin] = _hi_res_cover(session, album_data)
        tracks.append(
            _build_track(td, album_data, _disc_total(album_data), cover_cache[album_asin])
        )
    return tracks


def _playlist_from_data(
    session: AmazonMusicMobileAPI, p_data: dict, playlist_id: str,
    build_tracks: bool = True,
) -> Optional[PlaylistMetadata]:
    """Build a `PlaylistMetadata` from a playlist payload (catalog or user), or None
    if it carries no resolvable tracks. Both endpoints model a playlist as a dict
    with a `tracks` list and a `metadata.title`, so they share this builder.

    With `build_tracks=False` only the member `track_asins` + `name` are resolved
    (`tracks` left empty) — the caller builds the member metadata itself with
    progress. "No resolvable tracks" then means "no member ASINs at all"; the actual
    `TrackMetadata` build is deferred."""
    if not isinstance(p_data, dict):
        return None
    raw_tracks = p_data.get("tracks") or []
    track_asins = [a for a in (_playlist_track_asin(t) for t in raw_tracks) if a]
    if not track_asins:
        return None
    tracks = _build_tracks_from_asins(session, track_asins) if build_tracks else []
    if build_tracks and not tracks:
        return None
    meta = p_data.get("metadata") if isinstance(p_data.get("metadata"), dict) else {}
    name = (
        meta.get("title")
        or p_data.get("title")
        or p_data.get("name")
        or "Unknown Playlist"
    )
    return PlaylistMetadata(
        name=str(name), asin=str(playlist_id), track_asins=track_asins, tracks=tracks
    )


def try_fetch_playlist(
    session: AmazonMusicMobileAPI, playlist_asin: str, build_tracks: bool = True
) -> Optional[PlaylistMetadata]:
    """Resolve a catalog-playlist ASIN to a `PlaylistMetadata`, or None if the ASIN
    isn't a catalog playlist (so the caller can fall back / raise a combined error).
    `build_tracks=False` defers the member-metadata build (see `_playlist_from_data`)."""
    try:
        catalog = session.get_catalog_playlist(playlist_asin)
    except Exception:
        return None
    if not isinstance(catalog, dict):
        return None
    p_data = catalog.get("playlist")
    if not isinstance(p_data, dict):
        p_data = catalog
    return _playlist_from_data(session, p_data, playlist_asin, build_tracks)


def try_fetch_user_playlist(
    session: AmazonMusicMobileAPI, playlist_id: str, build_tracks: bool = True
) -> Optional[PlaylistMetadata]:
    """Resolve a user/library-playlist id (a uuid or library id from a
    `my/playlists/<id>` or `user-playlists/<id>` link) to a `PlaylistMetadata`, or
    None if it isn't a user playlist. Served by `getPlaylistsByIdV2`, which returns
    its playlist(s) under a `playlists` list rather than catalog's `playlist`.
    `build_tracks=False` defers the member-metadata build (see `_playlist_from_data`)."""
    try:
        resp = session.get_user_playlist(playlist_id)
    except Exception:
        return None
    if not isinstance(resp, dict):
        return None
    playlists = resp.get("playlists") or []
    if not playlists:
        return None
    return _playlist_from_data(session, playlists[0], playlist_id, build_tracks)
