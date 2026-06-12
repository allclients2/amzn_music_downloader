"""Download orchestration: dispatch an ASIN/batch to the right concurrency layout.

`download()` resolves one bare ASIN/link's kind (`fetch_metadata`) and routes it:

- **Single track (fast path)**: metadata, manifest, and lyrics all depend only on
  the ASIN, so they're fetched concurrently before the kind is known.
- **Album / playlist**: a flat set of tracks under an `asyncio.Semaphore`.
- **Artist**: `_download_artist` runs two phases on one bar — fetch every album's
  metadata up front (albums/s), then flatten and download every track (tracks/s).

`download_batch()` mirrors the artist's two-phase bar over a text file's inputs:
resolve every input to its tracks (input/s), then download them as one set
(tracks/s). The shared `_run_tracks` helper drives phase 2 everywhere; `_artist_tracks`
is shared by the artist path and the artist case of `_resolve_to_tracks`.
"""

import asyncio

from cli import ui
from cli.progress import Progress
from process.fetch_track import fetch_track, process_track, purge_temp_dir
from metadata.metadata import fetch_metadata
from metadata.mpd_info import fetch_representations, select_representation


def _note_skipped(results):
    """Emit a "N file(s) already exist; skipped." note when any track was skipped.

    `results` are the truthy/None values returned by process_track/fetch_track —
    True for a track whose output already existed.
    """
    skipped = sum(1 for r in results if r)
    if skipped:
        ui.note(f"{skipped} file(s) already exist; skipped.")


def _report_failures(failures, total):
    """Emit a "N of M input(s) failed:" summary when any batch input failed."""
    if failures:
        ui.note(f"{len(failures)} of {total} input(s) failed:")
        for asin, err in failures:
            ui.note(f"  {asin}: {err}")


async def download(session, asin, output_dir, quality, wvd_path="device.wvd", plain=False,
                   concurrency=5, metadata_concurrency=10):
    prog = Progress(asin=asin, plain=plain)
    prog.set_desc("fetching metadata, manifest & lyrics")

    try:
        # Single-track fast path: metadata, the DASH manifest, and lyrics all
        # depend only on the ASIN, so fetch them concurrently (we don't yet know
        # if the ASIN is a track or album). For an album ASIN the speculative
        # manifest/lyrics simply error and are ignored.
        meta_res, reps_res, lyrics_res = await asyncio.gather(
            asyncio.to_thread(fetch_metadata, session, asin),
            asyncio.to_thread(fetch_representations, session, asin, quality),
            asyncio.to_thread(session.get_track_lyrics, asin),
            return_exceptions=True,
        )

        if isinstance(meta_res, Exception):
            raise meta_res
        kind, meta = meta_res

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

        if kind == "track":
            prog.set_name(meta.title)
            representations = reps_res
            if isinstance(representations, Exception) or not representations:
                # Speculative manifest failed (rare for a track) — fetch directly.
                representations = fetch_representations(session, asin, quality)
            representation = select_representation(asin, representations, quality)
            lyrics_resp = None if isinstance(lyrics_res, Exception) else lyrics_res
            skipped = await process_track(
                session, meta, representation, output_dir, True, lyrics_resp,
                on_step=lambda desc: prog.update(desc), wvd_path=wvd_path,
            )
            prog.finish()
            _note_skipped([skipped])
        else:
            # Album or playlist: a flat set of tracks downloaded up to
            # `concurrency` at once. The aggregate line counts completed tracks;
            # each in-flight track gets its own step-progress slot line. A
            # playlist's tracks keep their own album tags, so each still lands
            # under `<album_artist>/<album>/`.
            prog.set_name(meta.album_name if kind == "album" else meta.name)
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
