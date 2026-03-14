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
    """Return cached config if it exists and is still fresh, else None."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE) as f:
            cached = json.load(f)
        if time.time() - cached["timestamp"] < CACHE_TTL:
            print("✅ Using cached config")
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


def run(task: str, asin: str, output_dir: str, result_queue):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

        from configs import fetch_configs, build_browser_with_cookies
        from metadata import Metadata, TrackMetadata, AlbumMetadata
        from metadata2 import Metadata2
        from fetch_track import fetch_track, download_temp_artwork, embed_metadata_and_cover
        from mpd_info import find_representation

        import requests
        from http.cookiejar import MozillaCookieJar
        from pathlib import Path

        cookies_file = "cookies.txt"
        output_path = Path(output_dir)

        # ── Step 1: Cookies ───────────────────────────────────────────────────
        session = requests.Session()
        jar = MozillaCookieJar(cookies_file)
        if os.path.exists(cookies_file):
            jar.load(ignore_discard=True, ignore_expires=True)
        session.cookies = jar

        # ── Step 2: Create the event loop first, run everything inside it ──────
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def main():
            nonlocal jar

            # Config (cached or fresh)
            config = _load_cached_config()

            if config is None:
                browser = await build_browser_with_cookies(session)
                config = await fetch_configs(browser)
                _save_cached_config(config)
                # Close browser sync — it's a sync Playwright object
                try:
                    browser["browser"].close()
                    browser["playwright"].stop()
                except Exception:
                    pass

            jar.save(ignore_discard=False, ignore_expires=False)

            # Metadata
            metadatav1 = Metadata.getMetadataFromEmbedLink(asin)
            album_metadatav2 = Metadata2.get_album_metadatav2(
                album_asin=metadatav1.album_asin,
                config=config
            )

            return config, metadatav1, album_metadatav2

        config, metadatav1, album_metadatav2 = loop.run_until_complete(main())

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
                    "release_date": album_metadatav2.release_date_iso if album_metadatav2 else None,
                    "label": album_metadatav2.label if album_metadatav2 else None,
                    "cover_art_url": album_metadatav2.cover_art_url if album_metadatav2 else None,
                }})
            return

        if task == "download_track":
            if not isinstance(metadatav1, TrackMetadata):
                result_queue.put({"ok": True, "data": {"error": "not_a_track"}})
                return

            tv2 = next((t for t in album_metadatav2.tracks if t.asin == metadatav1.track_asin), None)
            album_metadatav1 = metadatav1.fetch_disc_info()

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
                # Embed artwork after download
                mp4_files = list(output_path.rglob("*.mp4"))
                if mp4_files:
                    artwork_url = Metadata2.fetch_artwork_v2(metadatav1.track_asin, config)
                    import tempfile
                    with download_temp_artwork(artwork_url, tempfile.gettempdir()) as artwork_path:
                        embed_metadata_and_cover(
                            mp4_path=mp4_files[0],
                            track_metadatav1=metadatav1,
                            track_metadatav2=tv2,
                            album_metadatav1=album_metadatav1,
                            album_metadatav2=album_metadatav2,
                            artwork_path=artwork_path
                        )

            loop.run_until_complete(do_download_track())

            result_queue.put({"ok": True, "data": {
                "track_name": metadatav1.track_name,
                "artist_name": metadatav1.artist_name,
                "album_name": metadatav1.album_name,
            }})
            return

        if task == "download_album":
            # Always resolve to AlbumMetadata — if we got a track ASIN, fetch the album
            if isinstance(metadatav1, TrackMetadata):
                album_metadatav1 = Metadata.getMetadataFromEmbedLink(metadatav1.album_asin)
                if not isinstance(album_metadatav1, AlbumMetadata):
                    result_queue.put({"ok": False, "error": "Could not resolve album from track ASIN"})
                    return
            else:
                album_metadatav1 = metadatav1

            album_name = album_metadatav1.album_name
            artist_name = album_metadatav1.artist_name
            track_list_v1 = album_metadatav1.tracks

            async def do_download_album():
                import tempfile
                for index, track_metadatav1 in enumerate(track_list_v1):
                    tv2 = album_metadatav2.tracks[index] if index < len(album_metadatav2.tracks) else None

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

                    # Embed artwork for each track
                    mp4_files = list(output_path.rglob("*.mp4"))
                    # Find the most recently modified mp4 (the one just downloaded)
                    if mp4_files:
                        latest = max(mp4_files, key=lambda f: f.stat().st_mtime)
                        artwork_url = Metadata2.fetch_artwork_v2(track_metadatav1.track_asin, config)
                        with download_temp_artwork(artwork_url, tempfile.gettempdir()) as artwork_path:
                            embed_metadata_and_cover(
                                mp4_path=latest,
                                track_metadatav1=track_metadatav1,
                                track_metadatav2=tv2,
                                album_metadatav1=album_metadatav1,
                                album_metadatav2=album_metadatav2,
                                artwork_path=artwork_path
                            )

            loop.run_until_complete(do_download_album())

            result_queue.put({"ok": True, "data": {
                "album_name": album_name,
                "artist_name": artist_name,
                "track_count": len(track_list_v1),
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