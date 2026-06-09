import argparse
import asyncio
import logging
from pathlib import Path

import auth
from fetch_track import fetch_track, process_track
from metadata import fetch_metadata
from mpd_info import fetch_representations, select_representation
from progress import VERSION, Progress

# How many album tracks to download concurrently.
DOWNLOAD_CONCURRENCY = 5


def parse_args():
    parser = argparse.ArgumentParser(description="Download a track or album from Amazon Music")

    parser.add_argument(
        "content_asin",
        help="ASIN of the track or album to download",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"downloader v{VERSION}",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to save the file (default: current directory)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--min-bitrate",
        default=None,
        help="Minimum bitrate in kbps, or 'min' or 'max' (default: interactive picker)",
    )

    args = parser.parse_args()

    if args.min_bitrate and args.min_bitrate.isdigit():
        args.min_bitrate = int(args.min_bitrate)
    elif args.min_bitrate not in ("min", "max"):
        args.min_bitrate = None

    return args


async def main():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    # Builds a signed session from stored credentials; signs in interactively
    # (browser OAuth) on first use and persists the login.
    session = auth.get_session()

    output_dir = Path(args.output_dir)
    await download(session, args.content_asin, output_dir, args.min_bitrate, plain=args.verbose)


async def download(session, asin, output_dir, min_bitrate, plain=False):
    prog = Progress(asin=asin, plain=plain)
    prog.set_desc("fetching metadata, manifest & lyrics")

    try:
        # Single-track fast path: metadata, the DASH manifest, and lyrics all
        # depend only on the ASIN, so fetch them concurrently (we don't yet know
        # if the ASIN is a track or album). For an album ASIN the speculative
        # manifest/lyrics simply error and are ignored.
        meta_res, reps_res, lyrics_res = await asyncio.gather(
            asyncio.to_thread(fetch_metadata, session, asin),
            asyncio.to_thread(fetch_representations, session, asin),
            asyncio.to_thread(session.get_track_lyrics, asin),
            return_exceptions=True,
        )

        if isinstance(meta_res, Exception):
            raise meta_res
        kind, meta = meta_res

        if kind == "track":
            prog.set_name(meta.title)
            representations = reps_res
            if isinstance(representations, Exception) or not representations:
                # Speculative manifest failed (rare for a track) — fetch directly.
                representations = fetch_representations(session, asin)
            representation = select_representation(asin, representations, min_bitrate)
            lyrics_resp = None if isinstance(lyrics_res, Exception) else lyrics_res
            await process_track(
                session, meta, representation, output_dir, True, lyrics_resp,
                on_step=lambda desc: prog.update(desc),
            )
            prog.finish()
        else:
            # Album: download up to DOWNLOAD_CONCURRENCY tracks at once. The
            # aggregate line counts completed tracks; each in-flight track gets
            # its own step-progress slot line.
            prog.set_name(meta.album_name)
            prog.begin_album(len(meta.tracks))
            sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

            async def run_track(track):
                async with sem:
                    slot = prog.track_start(track.title)
                    try:
                        await fetch_track(
                            session, track, output_dir, min_bitrate,
                            on_step=lambda desc: prog.track_step(slot, desc),
                        )
                    finally:
                        prog.track_done(slot)

            await asyncio.gather(*(run_track(t) for t in meta.tracks))
            prog.finish()
    except Exception:
        prog.abort()
        raise


if __name__ == "__main__":
    asyncio.run(main())
