"""Download orchestration: dispatch an ASIN/batch to the right concurrency layout.

`download()` resolves one bare ASIN/link's kind (`fetch_metadata`) and routes it:

- **Single track (fast path)**: the manifest and lyrics are track-only requests, so
  they're fetched concurrently with metadata *only* when the input is known to be a
  track up front (a `type_hint`, e.g. a `/tracks/` link or a `track` search pick).
  Otherwise the kind is resolved from metadata first — the one request valid for any
  input — and the manifest/lyrics are fetched only once the track kind is confirmed,
  so they're never fired speculatively against an album/artist/playlist.
- **Album**: a flat set of tracks under an `asyncio.Semaphore`.
- **Artist / playlist**: `_download_artist` / `_download_playlist` run two phases on
  one bar — fetch every album's metadata up front (albums/s), then flatten and
  download every track (tracks/s). A playlist's phase 1 builds its member tracks: the
  member muse lookup returns each track's album in the *same* response (no second
  per-album round-trip), and the per-album cover search runs concurrently.

`download_batch()` mirrors the artist's two-phase bar over a text file's inputs:
resolve every input to its tracks (input/s), then download them as one set
(tracks/s). The shared `_run_tracks` helper drives phase 2 everywhere; `_artist_tracks`
is shared by the artist path and the artist case of `_resolve_to_tracks`.
"""

import asyncio

from cli import ui
from cli.progress import Progress
from process.fetch_track import fetch_track, process_track, purge_temp_dir
from metadata.metadata import (
    _BATCH_SIZE,
    _build_track,
    _disc_total,
    _fetch_album_data,
    _hi_res_cover,
    fetch_metadata,
    fetch_meta_chunk,
)
from metadata.mpd_info import fetch_representations, select_representation


def _note_skipped(results):
    """Emit a "N file(s) already existed; skipped." note when any track was skipped.

    `results` are the truthy/None values returned by process_track/fetch_track —
    True for a track whose output already existed.
    """
    skipped = sum(1 for r in results if r)
    if skipped:
        ui.note(f"{skipped} file(s) already existed; skipped.")


def _report_failures(failures, total):
    """Emit a "N of M input(s) failed:" summary when any batch input failed."""
    if failures:
        ui.note(f"{len(failures)} of {total} input(s) failed:")
        for asin, err in failures:
            ui.note(f"  {asin}: {err}")


async def _fetch_track_streams(session, asin, quality):
    """Fetch a track's DASH representations and lyrics concurrently — both are
    track-only requests, so this only runs once the input is known to be a track.
    Returns `(reps_or_exc, lyrics_or_exc)` (each may be an Exception, handled by the
    caller)."""
    return await asyncio.gather(
        asyncio.to_thread(fetch_representations, session, asin, quality),
        asyncio.to_thread(session.get_track_lyrics, asin),
        return_exceptions=True,
    )


async def download(session, asin, output_dir, quality, wvd_path="device.wvd", plain=False,
                   concurrency=5, metadata_concurrency=10, type_hint=None):
    prog = Progress(asin=asin, plain=plain)

    try:
        # The manifest and lyrics are track-only requests, so fire them concurrently
        # with metadata only when the input is *known* to be a track up front (a
        # 'track' type_hint — e.g. a /tracks/ or ?trackAsin= link, or a track search
        # pick); they're then guaranteed valid. For any other input we resolve the
        # kind from metadata first — the one request valid for every input type — so
        # the track-only calls are never fired speculatively against an album/
        # artist/playlist (which would just error and be discarded).
        track_streams = None  # (reps, lyrics) if prefetched alongside metadata
        if type_hint == "track":
            prog.set_desc("fetching metadata, manifest & lyrics")
            meta_res, track_streams = await asyncio.gather(
                asyncio.to_thread(
                    fetch_metadata, session, asin, defer_track_cover=True,
                    type_hint=type_hint,
                ),
                _fetch_track_streams(session, asin, quality),
            )
            kind, meta = meta_res
        else:
            # A 'playlist'/'user-playlist' hint lets fetch_metadata skip the doomed
            # muse lookup and hit the right playlist endpoint first. A playlist's
            # member tracks are left unbuilt (defer_playlist_tracks) so _download_
            # playlist can build them behind a progress bar.
            prog.set_desc("fetching metadata")
            kind, meta = await asyncio.to_thread(
                fetch_metadata, session, asin, defer_track_cover=True,
                type_hint=type_hint, defer_playlist_tracks=True,
            )

        if kind == "artist":
            # An artist resolves to a list of album ASINs (discovered from its
            # catalog page, not search). Hand off to a fresh two-phase bar (album
            # metadata, then track downloads); tear down this placeholder first.
            prog.abort()
            await _download_artist(
                session, asin, meta, output_dir, quality, wvd_path, plain,
                concurrency, metadata_concurrency
            )
            return

        if kind == "playlist":
            # A playlist resolved only to its member ASINs (deferred build). Hand off
            # to a fresh two-phase bar (member metadata, then track downloads) so the
            # build shows progress instead of blocking on this placeholder.
            prog.abort()
            await _download_playlist(
                session, asin, meta, output_dir, quality, wvd_path, plain,
                concurrency, metadata_concurrency
            )
            return

        if kind == "track":
            prog.set_name(meta.title)
            if track_streams is None:
                # Kind wasn't known up front, so it wasn't prefetched. Now that the
                # track kind is confirmed, fetch the manifest + lyrics (concurrently).
                prog.set_desc("fetching manifest & lyrics")
                track_streams = await _fetch_track_streams(session, asin, quality)
            reps_res, lyrics_res = track_streams
            representations = reps_res
            if isinstance(representations, Exception) or not representations:
                # Manifest fetch failed (rare for a track) — fetch directly.
                representations = fetch_representations(session, asin, quality)
            representation = select_representation(asin, representations, quality)
            lyrics_resp = None if isinstance(lyrics_res, Exception) else lyrics_res
            skipped = await process_track(
                session, meta, representation, output_dir, True, lyrics_resp,
                on_step=lambda desc: prog.update(desc), wvd_path=wvd_path,
                resolve_hi_res_cover=True,
            )
            prog.finish()
            _note_skipped([skipped])
        else:
            # Album: a flat set of tracks downloaded up to `concurrency` at once.
            # The aggregate line counts completed tracks; each in-flight track gets
            # its own step-progress slot line. (Playlists/artists are handled by the
            # two-phase branches above.)
            prog.set_name(meta.album_name)
            prog.begin_custom(len(meta.tracks))
            results = await _run_tracks(
                prog, session, meta.tracks, output_dir, quality, wvd_path, concurrency
            )
            prog.finish()
            _note_skipped(results)
    except Exception:
        prog.abort()
        raise
    finally:
        purge_temp_dir(output_dir)


async def _run_tracks(prog, session, tracks, output_dir, quality, wvd_path, concurrency):
    """Download a flat list of tracks under the multi-slot track view, up to
    `concurrency` at once. The caller has already switched the bar to the track
    phase via `prog.begin_custom(len(tracks), ...)`. Returns the per-track results
    (True = the output already existed and was skipped)."""
    sem = asyncio.Semaphore(concurrency)

    async def run_track(track):
        async with sem:
            slot = prog.track_start(track.title)
            try:
                return await fetch_track(
                    session, track, output_dir, quality,
                    on_step=lambda desc: prog.track_step(slot, desc),
                    wvd_path=wvd_path,
                )
            finally:
                prog.track_done(slot)

    return await asyncio.gather(*(run_track(t) for t in tracks))


async def _artist_tracks(session, artist, metadata_concurrency, on_album=None):
    """Fetch every album's tracks for an artist, `metadata_concurrency` at a time,
    flattened into one track list (album order preserved). A failed album metadata
    lookup is skipped so the rest of the discography still downloads. `on_album`, if
    given, is called once per album as its metadata resolves (drives an aggregate
    progress bar)."""
    sem = asyncio.Semaphore(max(1, metadata_concurrency))
    metas = []

    async def fetch_one(album_asin):
        async with sem:
            try:
                kind, meta = await asyncio.to_thread(fetch_metadata, session, album_asin)
                if kind == "album" and getattr(meta, "tracks", None):
                    metas.append(meta)
            except Exception:
                pass  # one bad album shouldn't sink the discography
            finally:
                if on_album:
                    on_album()

    await asyncio.gather(*(fetch_one(a) for a in artist.album_asins))
    return [track for meta in metas for track in meta.tracks]


async def _resolve_to_tracks(session, asin, metadata_concurrency):
    """Resolve one input id to a flat list of tracks, expanding albums/playlists to
    their members and an artist to its whole discography. Raises if the id doesn't
    resolve, so the batch caller can record it as a failed input."""
    kind, meta = await asyncio.to_thread(fetch_metadata, session, asin)
    if kind == "track":
        return [meta]
    if kind in ("album", "playlist"):
        return list(meta.tracks)
    if kind == "artist":
        return await _artist_tracks(session, meta, metadata_concurrency)
    return []


async def _download_artist(session, asin, artist, output_dir, quality, wvd_path,
                           plain, concurrency, metadata_concurrency):
    """Download an artist's whole discography in two phases under one progress bar:

    1. Fetch every album's metadata up front, `metadata_concurrency` at a time — a
       single aggregate bar counting albums (albums/s).
    2. Flatten all albums into one track list and download them like a big album —
       the multi-slot track view (tracks/s), `concurrency` tracks at a time.

    A failed album metadata lookup is skipped so the rest of the discography still
    downloads."""
    albums = artist.album_asins
    if not albums:
        ui.note(f"No albums found for artist '{artist.name}'.")
        return

    prog = Progress(asin=asin, plain=plain)
    prog.set_name(artist.name)
    try:
        # ── Phase 1: fetch all album metadata (albums/s) ──────────────────────
        prog.begin_custom(len(albums), rate_label="albums/s")
        tracks = await _artist_tracks(
            session, artist, metadata_concurrency, on_album=prog.advance_aggregate
        )
        if not tracks:
            prog.abort()
            ui.note(f"No downloadable tracks found for artist '{artist.name}'.")
            return

        # ── Phase 2: download every track (tracks/s) ──────────────────────────
        prog.begin_custom(len(tracks), rate_label="tracks/s")
        results = await _run_tracks(
            prog, session, tracks, output_dir, quality, wvd_path, concurrency
        )
        prog.finish()
        _note_skipped(results)
    except Exception:
        prog.abort()
        raise
    finally:
        purge_temp_dir(output_dir)


async def _playlist_member_meta(session, track_asins, metadata_concurrency):
    """Phase-1 prelude for a playlist: fetch every member's track muse data
    (`metadata_concurrency` chunks at once). The album rides along in the *same*
    response as the track (`fetch_meta_chunk`), so album data is collected here too —
    no separate per-album round-trip. Returns `(rich, albums, by_album)` where `rich`
    maps each member ASIN to its track data, `albums` maps album ASIN to album data,
    and `by_album` maps each album ASIN to its member ASINs in playlist order."""
    sem = asyncio.Semaphore(max(1, metadata_concurrency))
    rich: dict = {}
    albums: dict = {}
    chunks = [track_asins[i:i + _BATCH_SIZE]
              for i in range(0, len(track_asins), _BATCH_SIZE)]

    async def fetch_chunk(chunk):
        async with sem:
            tracks, alb = await asyncio.to_thread(fetch_meta_chunk, session, chunk)
            rich.update(tracks)
            albums.update(alb)

    await asyncio.gather(*(fetch_chunk(c) for c in chunks))

    by_album: dict = {}
    for asin in track_asins:
        td = rich.get(asin)
        album_asin = (td.get("album") or {}).get("asin") if td else None
        if album_asin:
            by_album.setdefault(album_asin, []).append(asin)
    return rich, albums, by_album


async def _build_playlist_tracks(session, rich, albums, by_album, track_asins,
                                 metadata_concurrency, on_album=None):
    """Phase-1 build for a playlist: per album (concurrently, `metadata_concurrency`
    at a time) resolve its hi-res cover and build its member tracks, then flatten back
    into playlist order. `on_album` ticks once per album resolved, driving the albums/s
    aggregate exactly like the artist's album-metadata phase."""
    sem = asyncio.Semaphore(max(1, metadata_concurrency))
    built: dict = {}

    async def build_album(album_asin, members):
        async with sem:
            album_data = albums.get(album_asin) or await asyncio.to_thread(
                _fetch_album_data, session, album_asin
            )
            cover = await asyncio.to_thread(_hi_res_cover, session, album_data)
            disc_total = _disc_total(album_data)
            for asin in members:
                if asin in rich:
                    built[asin] = _build_track(rich[asin], album_data, disc_total, cover)
        if on_album:
            on_album()

    await asyncio.gather(*(build_album(a, m) for a, m in by_album.items()))
    return [built[a] for a in track_asins if a in built]


async def _download_playlist(session, asin, playlist, output_dir, quality, wvd_path,
                             plain, concurrency, metadata_concurrency):
    """Download a playlist's member tracks in two phases under one progress bar,
    mirroring the artist layout:

    1. Build every member's metadata up front. A quick concurrent prelude fetches all
       member track+album muse data (the album rides along in the same response, so no
       second per-album round-trip), then a single aggregate bar ticks as each album's
       cover resolves and its tracks are built (albums/s).
    2. Flatten every member into one track list and download them like a big album —
       the multi-slot track view (tracks/s), `concurrency` tracks at a time.

    Each member track keeps its own album tags, so it still files under
    `<album_artist>/<album>/`."""
    track_asins = playlist.track_asins
    if not track_asins:
        ui.note(f"No tracks found in playlist '{playlist.name}'.")
        return

    prog = Progress(asin=asin, plain=plain)
    prog.set_name(playlist.name)
    try:
        # ── Phase 1: build every member's metadata (albums/s) ─────────────────
        prog.set_desc("fetching metadata")
        rich, albums, by_album = await _playlist_member_meta(
            session, track_asins, metadata_concurrency
        )
        prog.begin_custom(len(by_album), rate_label="albums/s")
        tracks = await _build_playlist_tracks(
            session, rich, albums, by_album, track_asins, metadata_concurrency,
            on_album=prog.advance_aggregate,
        )
        if not tracks:
            prog.abort()
            ui.note(f"No downloadable tracks found in playlist '{playlist.name}'.")
            return

        # ── Phase 2: download every track (tracks/s) ──────────────────────────
        prog.begin_custom(len(tracks), rate_label="tracks/s")
        results = await _run_tracks(
            prog, session, tracks, output_dir, quality, wvd_path, concurrency
        )
        prog.finish()
        _note_skipped(results)
    except Exception:
        prog.abort()
        raise
    finally:
        purge_temp_dir(output_dir)


async def download_batch(session, source_label, asins, output_dir, quality, wvd_path,
                         plain, concurrency, metadata_concurrency):
    """Download a text-file batch of inputs in two phases under one progress bar,
    mirroring the artist layout:

    1. Resolve every input to its tracks — a single aggregate bar counting inputs
       (input/s). Albums/playlists expand to their members and an artist to its whole
       discography; each input still counts as one toward the aggregate.
    2. Flatten every input's tracks into one list and download them like a big album —
       the multi-slot track view (tracks/s), `concurrency` tracks at a time.

    A failed input is skipped (collected and summarised at the end) so one bad entry
    doesn't sink the rest of the batch."""
    prog = Progress(asin=source_label, plain=plain)
    failures = []
    try:
        # ── Phase 1: resolve every input to its tracks (input/s) ──────────────
        prog.begin_custom(len(asins), rate_label="input/s")
        sem = asyncio.Semaphore(max(1, metadata_concurrency))
        per_input = [None] * len(asins)

        async def resolve_one(idx, asin):
            async with sem:
                try:
                    per_input[idx] = await _resolve_to_tracks(
                        session, asin, metadata_concurrency
                    )
                except Exception as exc:
                    failures.append((asin, str(exc) or type(exc).__name__))
                finally:
                    prog.advance_aggregate()

        await asyncio.gather(*(resolve_one(i, a) for i, a in enumerate(asins)))

        # Flatten the inputs into one track list, preserving input order.
        tracks = [track for group in per_input if group for track in group]
        if not tracks:
            prog.abort()
            ui.note("No downloadable tracks found in input.")
            _report_failures(failures, len(asins))
            return

        # ── Phase 2: download every track (tracks/s) ──────────────────────────
        prog.begin_custom(len(tracks), rate_label="tracks/s")
        results = await _run_tracks(
            prog, session, tracks, output_dir, quality, wvd_path, concurrency
        )
        prog.finish()
        _note_skipped(results)
        _report_failures(failures, len(asins))
    except Exception:
        prog.abort()
        raise
    finally:
        purge_temp_dir(output_dir)
