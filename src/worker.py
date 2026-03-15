"""
Standalone worker — runs in a child process.
Playwright (sync) is called BEFORE any event loop is created.
fetch_track (async) is called AFTER, in its own loop.
"""
import traceback
import os
import sys
import json
import time


CACHE_FILE = "config_cache.json"
CACHE_TTL  = 3300  # 55 min — slightly under the 60 min token expiry


def _load_cached_config():
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            cached = json.load(f)
        if time.time() - cached["timestamp"] < CACHE_TTL:
            return cached["config"]
        print("⏳ Config cache expired, refetching…")
    except Exception as e:
        print(f"⚠️ Could not read config cache: {e}")
    return None


def _save_cached_config(config):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({"timestamp": time.time(), "config": config}, f)
    except Exception as e:
        print(f"⚠️ Could not save config cache: {e}")


def run(task: str, asin: str, output_dir: str, result_queue, progress_queue=None):
    def progress(msg: str):
        print(msg)
        if progress_queue:
            progress_queue.put(msg)

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        from configs import fetch_configs, build_browser_with_cookies
        from metadata import Metadata, TrackMetadata, AlbumMetadata
        from metadata2 import Metadata2
        from fetch_track import fetch_track
        from mpd_info import find_representation

        import requests
        from http.cookiejar import MozillaCookieJar
        from pathlib import Path

        cookies_file = "cookies.txt"
        output_path = Path(output_dir)

        # ── Cookies ───────────────────────────────────────────────────────────
        session = requests.Session()
        jar = MozillaCookieJar(cookies_file)
        if os.path.exists(cookies_file):
            jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies = jar

        # ── Event loop ────────────────────────────────────────────────────────
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main():
            nonlocal jar

            # Config
            config = _load_cached_config()
            if config is None:
                progress("🔧 Launching browser & fetching config…")
                browser = await build_browser_with_cookies(session)
                config = await fetch_configs(browser)
                _save_cached_config(config)
                try:
                    await browser["browser"].close()
                    await browser["playwright"].stop()
                except Exception:
                    pass
            else:
                progress("✅ Using cached config")

            jar.save(ignore_discard=False, ignore_expires=False)

            # Metadata
            progress("🔍 Fetching metadata…")
            metadatav1 = Metadata.getMetadataFromEmbedLink(asin)
            album_metadatav2 = Metadata2.get_album_metadatav2(
                album_asin=metadatav1.album_asin,
                config=config
            )

            return config, metadatav1, album_metadatav2

        config, metadatav1, album_metadatav2 = loop.run_until_complete(main())

        # ── Auto-detect type ──────────────────────────────────────────────────
        from metadata import AlbumMetadata, TrackMetadata

        if task == "auto":
            if isinstance(metadatav1, AlbumMetadata):
                task = "download_album"
            else:
                task = "download_track"

        # ── Metadata only ─────────────────────────────────────────────────────
        if task == "metadata":
            if isinstance(metadatav1, TrackMetadata):
                tv2 = next((t for t in album_metadatav2.tracks if t.asin == metadatav1.track_asin), None)
                result_queue.put({"ok": True, "data": {
                    "type": "track",
                    "track_name": metadatav1.track_name,
                    "artist_name": metadatav1.artist_name,
                    "album_name": metadatav1.album_name,
                    "track_asin": metadatav1.track_asin,
                    "album_asin": metadatav1.album_asin,
                    "duration": f"{tv2.duration_seconds // 60}:{tv2.duration_seconds % 60:02d}" if tv2 else None,
                    "is_explicit": tv2.is_explicit if tv2 else None,
                    "lyrics_available": tv2.lyrics_available if tv2 else None,
                    "cover_art_url": album_metadatav2.cover_art_url if album_metadatav2 else None,
                }})
            else:
                tracks = metadatav1.tracks
                result_queue.put({"ok": True, "data": {
                    "type": "album",
                    "album_name": metadatav1.album_name,
                    "artist_name": metadatav1.artist_name,
                    "album_asin": metadatav1.album_asin,
                    "track_count": len(tracks),
                    "tracks": [t.track_name for t in tracks],
                    "tracks_detailed": [
                        {
                            "disc": getattr(t, "disc", 1) or 1,
                            "track_number": getattr(t, "track_number", i + 1) or (i + 1),
                            "name": t.track_name,
                        }
                        for i, t in enumerate(tracks)
                    ],
                    "release_date": album_metadatav2.release_date_iso if album_metadatav2 else None,
                    "label": album_metadatav2.label if album_metadatav2 else None,
                    "cover_art_url": album_metadatav2.cover_art_url if album_metadatav2 else None,
                }})
            loop.close()
            return

        # ── Download track ────────────────────────────────────────────────────
        if task == "download_track":
            if not isinstance(metadatav1, TrackMetadata):
                result_queue.put({"ok": False, "error": "Expected a track ASIN but got an album."})
                loop.close()
                return

            tv2 = next((t for t in album_metadatav2.tracks if t.asin == metadatav1.track_asin), None)
            album_metadatav1 = metadatav1.fetch_disc_info()

            progress(f"🎵 Downloading track: {metadatav1.track_name}…")

            representation = find_representation(
                track_asin=metadatav1.track_asin,
                config=config,
                cookie_header=None,
                min_bitrate="max"
            )

            async def do_download_track():
                await fetch_track(
                    track_representation=representation,
                    track_metadatav1=metadatav1,
                    track_metadatav2=tv2,
                    album_metadatav1=album_metadatav1,
                    album_metadatav2=album_metadatav2,
                    output_dir=output_path,
                    config=config,
                    cookie_header=None,
                    build_folder_structure=False
                )


            loop.run_until_complete(do_download_track())
            loop.close()

            result_queue.put({"ok": True, "data": {
                "type": "track",
                "track_name": metadatav1.track_name,
                "artist_name": metadatav1.artist_name,
                "album_name": metadatav1.album_name,
                "cover_art_url": album_metadatav2.cover_art_url if album_metadatav2 else None,
            }})
            return

        # ── Download album ────────────────────────────────────────────────────
        if task == "download_album":
            if isinstance(metadatav1, TrackMetadata):
                album_metadatav1 = Metadata.getMetadataFromEmbedLink(metadatav1.album_asin)
                if not isinstance(album_metadatav1, AlbumMetadata):
                    result_queue.put({"ok": False, "error": "Could not resolve album from track ASIN"})
                    loop.close()
                    return
            else:
                album_metadatav1 = metadatav1

            album_name = album_metadatav1.album_name
            artist_name = album_metadatav1.artist_name
            track_list_v1 = album_metadatav1.tracks
            total = len(track_list_v1)

            progress(f"💿 Downloading album: {album_name} ({total} tracks)…")

            async def do_download_album():
                import tempfile
                for index, track_metadatav1 in enumerate(track_list_v1):
                    tv2 = album_metadatav2.tracks[index] if index < len(album_metadatav2.tracks) else None
                    progress(f"⬇️  [{index + 1}/{total}] {track_metadatav1.track_name}…")

                    representation = find_representation(
                        track_asin=track_metadatav1.track_asin,
                        config=config,
                        cookie_header=None,
                        min_bitrate="max"
                    )

                    await fetch_track(
                        track_representation=representation,
                        track_metadatav1=track_metadatav1,
                        track_metadatav2=tv2,
                        album_metadatav1=album_metadatav1,
                        album_metadatav2=album_metadatav2,
                        output_dir=output_path,
                        config=config,
                        cookie_header=None,
                        build_folder_structure=False
                    )



            loop.run_until_complete(do_download_album())
            loop.close()

            result_queue.put({"ok": True, "data": {
                "type": "album",
                "album_name": album_name,
                "artist_name": artist_name,
                "track_count": total,
                "cover_art_url": album_metadatav2.cover_art_url if album_metadatav2 else None,
            }})
            return

        loop.close()
        result_queue.put({"ok": False, "error": f"Unknown task: {task}"})

    except Exception as e:
        result_queue.put({"ok": False, "error": str(e), "tb": traceback.format_exc()})


def _close_browser(browser):
    try:
        browser["browser"].close()
        browser["playwright"].stop()
    except Exception:
        pass