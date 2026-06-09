import argparse
import asyncio
import logging
from pathlib import Path

import auth
import config
from fetch_track import fetch_track, process_track, purge_temp_dir
from metadata import fetch_metadata
from mpd_info import fetch_representations, select_representation
from _version import VERSION
from progress import Progress

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
        "--output",
        default=None,
        help="Directory to save the file (default: config default_output)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--default-quality",
        default=None,
        help="Max quality tier to use: SD, HD, or UHD (default: config default_quality)",
    )
    parser.add_argument(
        "--wvd-path",
        default=None,
        help="Path to the Widevine device file (default: config default_wvd_path)",
    )

    return parser.parse_args()


async def main():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    # CLI flags override the stored config defaults (generated on first run).
    settings = config.get_settings()
    quality = args.default_quality or settings["default_quality"]
    wvd_path = args.wvd_path or settings["default_wvd_path"]
    output_dir = Path(args.output or settings["default_output"])

    # Builds a signed session from stored credentials; signs in interactively
    # (browser OAuth) on first use and persists the login.
    session = auth.get_session()

    await download(session, args.content_asin, output_dir, quality, wvd_path, plain=args.verbose)


async def download(session, asin, output_dir, quality, wvd_path="device.wvd", plain=False):
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
            representation = select_representation(asin, representations, quality)
            lyrics_resp = None if isinstance(lyrics_res, Exception) else lyrics_res
            await process_track(
                session, meta, representation, output_dir, True, lyrics_resp,
                on_step=lambda desc: prog.update(desc), wvd_path=wvd_path,
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
                            session, track, output_dir, quality,
                            on_step=lambda desc: prog.track_step(slot, desc),
                            wvd_path=wvd_path,
                        )
                    finally:
                        prog.track_done(slot)

            await asyncio.gather(*(run_track(t) for t in meta.tracks))
            prog.finish()
    except Exception:
        prog.abort()
        raise
    finally:
        purge_temp_dir(output_dir)


if __name__ == "__main__":
    asyncio.run(main())
