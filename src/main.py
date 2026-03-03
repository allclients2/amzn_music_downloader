import argparse
import requests
import os
import re
import subprocess
import tempfile

from pathlib import Path
from contextlib import contextmanager
from configs import Configs
from cookies import Cookies
from media_utils import MediaUtils
from keys import Keys
from metadata import Metadata
from mpd_info import MpdInfo
from mpd_selector import MPDStreamSelector
from cookies import Cookies, CookieError
from mutagen.mp4 import MP4, MP4Cover
from metadata2 import Metadata2
from metadata_objects import AlbumMetadata, TrackMetadata
from lyrics import Lyrics
from concurrent.futures import ThreadPoolExecutor

def sanitize_filename(name: str) -> str:
    """Make filename OS safe."""
    return re.sub(r'[<>:"/\\|?*]', "", name)


def build_output_filename(track_name: str, artist_name: str) -> str:
    filename = f"{track_name} - {artist_name}"
    return sanitize_filename(filename)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download and decrypt a DRM protected track"
    )

    parser.add_argument(
        "content_asin",
        help="ASIN of the track (primary identifier)"
    )

    parser.add_argument(
        "-o", "--output",
        help="Output filename (default: auto-generated)"
    )

    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to save the file (default: current directory)"
    )

    parser.add_argument(
        "--cookies-file",
        default="cookies.txt",
        help="Path to Netscape cookies file"
    )

    parser.add_argument(
        "--keep-encrypted",
        action="store_true",
        help="Keep encrypted temporary file"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--from-browser",
        action="store_true",
        help="Load cookies directly from browser"
    )

    parser.add_argument(
        "--browser",
        default="chrome",
        choices=["chrome", "edge", "firefox"],
        help="Browser to extract cookies from (default: chrome)"
    )

    return parser.parse_args()

def download_artwork(url: str, path: str):
    response = requests.get(url)
    response.raise_for_status()
    with open(path, "wb") as f:
        f.write(response.content)

def embed_metadata_and_cover(
    mp4_path: str,
    album: AlbumMetadata,
    track: TrackMetadata,
    artwork_path: str | None
):
    audio = MP4(mp4_path)

    # track metadata
    audio["\xa9nam"] = track.title
    audio["\xa9ART"] = track.artist
    audio["trkn"] = [(album.tracks.index(track) + 1, album.track_count)]
    audio["\xa9day"] = album.release_date_iso or ""
    audio["\xa9cmt"] = "explicit" if track.is_explicit else ""

    # album metadata
    audio["\xa9alb"] = album.name
    audio["aART"] = album.artist
    audio["\xa9cpy"] = album.copyright or ""

    # lyrics
    if track.lyrics and track.lyrics.has_content():
        audio["\xa9lyr"] = track.lyrics.to_mp4_lyrics()

    # popularity via freeform atom
    if track.popularity is not None:
        audio["----:com.apple.iTunes:POPULARITY"] = [
            str(track.popularity).encode("utf-8")
        ]

    # artwork / coverart
    if artwork_path:
        with open(artwork_path, "rb") as img:
            cover = img.read()

        audio["covr"] = [
            MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)
        ]

    audio.save()

@contextmanager
def download_temp_artwork(url: str):
    if not url:
        yield None
        return

    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        tmp.write(response.content)
        tmp_path = tmp.name

    try:
        yield tmp_path
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def main():
    args = parse_args()
    
    try:
        if args.from_browser:
            print("loading cookies from browser...")
            cookie_header = Cookies.from_browser(
                domain="amazon.co.jp",
                browser=args.browser
            )
        else:
            cookie_header = Cookies.netscape_to_cookie_header(args.cookies_file)
    except CookieError as e:
        print(str(e))
        return
    
    content_asin = args.content_asin
    config = Configs.fetch_configs(cookie_header)

    print("fetching track MPD streams...")
    manifest_xml = MpdInfo.getTrackInfo(content_asin, config, cookie_header)
    selector = MPDStreamSelector(manifest_xml)

    result = selector.select()

    if not result:
        print("no track selection made.")
        return

    if args.verbose:
        print(f"selected stream: {result}")

    rep = next(
        r for r in selector.representations
        if r["base_url"] == result["base_url"]
    )

    print("fetching base metadata...")
    metadatav1 = Metadata.getTrackMetadataFromEmbedLink(content_asin)

    track_name = metadatav1["track_name"]
    artist_name = metadatav1["artist_name"]

    output_filename = build_output_filename(track_name, artist_name)

    if args.output:
        output_file = Path(args.output)
    else:
        output_file = Path(args.output_dir) / (output_filename + ".mp4")

    print("downloading encrypted file...")
    encrypted_file = Path("encrypted.mp4")
    selector.download_full_file(rep, encrypted_file)

    print("fetching content keys...")
    content_key = Keys.getContentKeys(result["pssh"], config, cookie_header)

    print("decrypting...")
    unicode_output = output_file
    temp_output = Path("decrypted_temp.mp4")

    cmd = [
        "mp4decrypt",
        "--key", content_key,
        str(encrypted_file),
        str(temp_output)
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("decryption failed?")
        return

    print("fetching extra metadata...")

    with ThreadPoolExecutor(max_workers=2) as executor:
        album_future = executor.submit(
            Metadata2.get_album_metadata,
            metadatav1["album_asin"],
            config
        )
        artwork_future = executor.submit(
            Metadata2.fetch_artwork_v2,
            content_asin,
            config
        )

        album = album_future.result()
        artwork_url = artwork_future.result()

    track = next((t for t in album.tracks if t.asin == content_asin), None)

    if not track:
        raise ValueError(f"track {content_asin} not found in corresponding album {album.asin}")

    if track.lyrics_available:
        with ThreadPoolExecutor(max_workers=1) as executor:
            lyrics_future = executor.submit(
                Metadata2.fetch_lyrics,
                track.asin,
                track.duration_seconds,
                config
            )
            json_lyrics = lyrics_future.result()

        lyrics_obj = Lyrics.from_json(json_lyrics)
        track.attach_lyrics(lyrics_obj)

    artwork_url = artwork_url or album.cover_art_url

    with download_temp_artwork(artwork_url) as artwork_path:
        embed_metadata_and_cover(
            mp4_path=temp_output,
            album=album,
            track=track,
            artwork_path=artwork_path
        )

    temp_output.rename(unicode_output)

    if not args.keep_encrypted:
        encrypted_file.unlink(missing_ok=True)

    if track.lyrics and track.lyrics.has_content():
        track.lyrics.save_lrc(output_filename + ".lrc")

    print(f"finished, saved to: {output_file}")

if __name__ == "__main__":
    main()