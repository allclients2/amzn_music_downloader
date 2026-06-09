"""
Standalone worker — runs in a child process for the Discord bot.

Uses the same signed `AmazonMusicMobileAPI` session + muse metadata as the CLI.
The login (browser OAuth) must be performed once via `python src/main.py <asin>`;
this worker only loads/refreshes the stored credentials and never prompts.
"""
import asyncio
import os
import sys
import traceback
from pathlib import Path


def _fmt_duration(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def run(task: str, asin: str, output_dir: str, result_queue, progress_queue=None):
    def progress(msg: str):
        print(msg)
        if progress_queue:
            progress_queue.put(msg)

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        import auth
        import config
        from fetch_track import fetch_track, purge_temp_dir
        from metadata import fetch_metadata

        output_path = Path(output_dir)
        settings = config.get_settings()
        quality = settings["default_quality"]
        wvd_path = settings["default_wvd_path"]

        progress("🔐 Loading Amazon Music session…")
        session = auth.get_session(interactive=False)

        progress("🔍 Fetching metadata…")
        kind, meta = fetch_metadata(session, asin)

        # ── Auto-detect type ──────────────────────────────────────────────────
        if task == "auto":
            task = "download_album" if kind == "album" else "download_track"

        # ── Metadata only ─────────────────────────────────────────────────────
        if task == "metadata":
            if kind == "track":
                result_queue.put({"ok": True, "data": {
                    "type": "track",
                    "track_name": meta.title,
                    "artist_name": meta.artist,
                    "album_name": meta.album_name,
                    "track_asin": meta.asin,
                    "album_asin": meta.album_asin,
                    "duration": _fmt_duration(meta.duration_seconds),
                    "is_explicit": meta.is_explicit,
                    "cover_art_url": meta.cover_url,
                }})
            else:
                result_queue.put({"ok": True, "data": {
                    "type": "album",
                    "album_name": meta.album_name,
                    "artist_name": meta.artist_name,
                    "album_asin": meta.album_asin,
                    "track_count": len(meta.tracks),
                    "tracks": [t.title for t in meta.tracks],
                    "tracks_detailed": [
                        {"disc": t.disc, "track_number": t.track_number, "name": t.title}
                        for t in meta.tracks
                    ],
                    "release_date": meta.release_date,
                    "label": meta.label,
                    "cover_art_url": meta.cover_url,
                }})
            return

        # ── Download track ────────────────────────────────────────────────────
        if task == "download_track":
            if kind != "track":
                result_queue.put({"ok": False, "error": "Expected a track ASIN but got an album."})
                return

            progress(f"🎵 Downloading track: {meta.title}…")
            asyncio.run(
                fetch_track(
                    session, meta, output_path, quality,
                    build_folder_structure=False, wvd_path=wvd_path,
                )
            )
            purge_temp_dir(output_path)

            result_queue.put({"ok": True, "data": {
                "type": "track",
                "track_name": meta.title,
                "artist_name": meta.artist,
                "album_name": meta.album_name,
                "cover_art_url": meta.cover_url,
            }})
            return

        # ── Download album ────────────────────────────────────────────────────
        if task == "download_album":
            if kind != "album":
                result_queue.put({"ok": False, "error": "Could not resolve album from ASIN"})
                return

            total = len(meta.tracks)
            progress(f"💿 Downloading album: {meta.album_name} ({total} tracks)…")

            async def do_download_album():
                for index, track in enumerate(meta.tracks):
                    progress(f"⬇️  [{index + 1}/{total}] {track.title}…")
                    await fetch_track(
                        session, track, output_path, quality,
                        build_folder_structure=False, wvd_path=wvd_path,
                    )

            asyncio.run(do_download_album())
            purge_temp_dir(output_path)

            result_queue.put({"ok": True, "data": {
                "type": "album",
                "album_name": meta.album_name,
                "artist_name": meta.artist_name,
                "track_count": total,
                "cover_art_url": meta.cover_url,
            }})
            return

        result_queue.put({"ok": False, "error": f"Unknown task: {task}"})

    except Exception as e:
        result_queue.put({"ok": False, "error": str(e), "tb": traceback.format_exc()})
