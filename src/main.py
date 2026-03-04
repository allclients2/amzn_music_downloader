import argparse
import requests
import os
import re
import subprocess
import tempfile
import shutil

from pathlib import Path
from contextlib import contextmanager
from configs import Configs
from cookies import Cookies
from keys import Keys
from metadata import Metadata, TrackMetadata, AlbumMetadata
from mpd_info import MpdInfo
from mpd_selector import MPDStreamSelector
from cookies import Cookies, CookieError
from mutagen.mp4 import MP4, MP4Cover
from metadata2 import Metadata2, AlbumMetadataV2, TrackMetadataV2
from lyrics import Lyrics

ILLEGAL_CHARS_RE = r'[\\/:*?"<>|;]'

def safe_filename(name, has_file_ext=False):
    sanitized = re.sub(ILLEGAL_CHARS_RE, "_", name)
    if not has_file_ext:
        if sanitized.endswith(".."):
            sanitized = sanitized[:-1] + "_"
        elif sanitized.endswith("."):
            sanitized = sanitized[:-1]
    return sanitized.strip()

def build_output_filename(disc_number: str, track_num: int, track_name: str) -> str:
    filename = f"{disc_number} - {track_num} {track_name}"
    return safe_filename(filename, True)

def download_artwork(url: str, path: str):
    response = requests.get(url)
    response.raise_for_status()
    with open(path, "wb") as f:
        f.write(response.content)

def embed_metadata_and_cover(
    mp4_path: str,
    track_metadatav1: TrackMetadata,
    track_metadatav2: TrackMetadataV2,
    album_metadatav2: AlbumMetadataV2,
    artwork_path: str | None
):
    audio = MP4(mp4_path)
    
    # disc info
    disc_number = track_metadatav1.disc if hasattr(track_metadatav1, "disc") else 1

    # tracks on this disc only
    tracks_on_disc = [
        t for t in album_metadatav2.tracks
        if getattr(t, "disc", 1) == disc_number
    ]

    total_tracks_on_disc = len(tracks_on_disc)
    total_discs = max(
        (getattr(t, "disc", 1) for t in album_metadatav2.tracks),
        default=1
    )

    # track metadata
    audio["\xa9nam"] = track_metadatav2.title
    audio["\xa9ART"] = track_metadatav2.artist
    audio["trkn"] = [(track_metadatav1.track_number, total_tracks_on_disc)]
    audio["disk"] = [(track_metadatav1.disc, total_discs)]
    audio["\xa9day"] = album_metadatav2.release_date_iso or ""
    audio["\xa9cmt"] = "explicit" if track_metadatav2.is_explicit else ""

    # album metadata
    audio["\xa9alb"] = album_metadatav2.name
    audio["aART"] = album_metadatav2.artist
    audio["\xa9cpy"] = album_metadatav2.copyright or ""

    # lyrics
    if track_metadatav2.lyrics and track_metadatav2.lyrics.has_content():
        audio["\xa9lyr"] = track_metadatav2.lyrics.to_mp4_lyrics()

    # popularity
    if track_metadatav2.popularity is not None:
        audio["----:com.apple.iTunes:POPULARITY"] = [
            str(track_metadatav2.popularity).encode("utf-8")
        ]

    # artwork
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

def parse_args():
    parser = argparse.ArgumentParser(
        description="Download and decrypt a DRM protected track"
    )

    parser.add_argument(
        "content_asin",
        help="ASIN of the track (primary identifier)"
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

    parser.add_argument(
        "--min-bitrate",
        default=None,
        help="Minimum bitrate in kbps, or 'min' or 'max' (default: max)"
    )

    args = parser.parse_args()

    if args.min_bitrate and args.min_bitrate.isdigit():
        args.min_bitrate = int(args.min_bitrate)
    elif args.min_bitrate not in ("min", "max"):
        args.min_bitrate = None

    return args

def fetch_track(
    track_asin: str,
    track_metadatav1: TrackMetadata,
    track_metadatav2: TrackMetadataV2,
    album_metadatav2: AlbumMetadataV2,
    output_dir: Path,
    config,
    cookie_header: str,
    min_bitrate: str
):
    print("fetching track MPD streams...")
    try:
        manifest_xml = MpdInfo.getTrackInfo(track_asin, config, cookie_header)
    except KeyError:
        print("failed to fetch MPD streams. try reloading the website or refetching cookies.")
    selector = MPDStreamSelector(manifest_xml)

    representations = selector.representations
    if not representations:
        print("no available representations.")
        return

    if min_bitrate == "max":
        rep = max(representations, key=lambda r: int(r["bandwidth"]))
    elif min_bitrate == "min":
        rep = min(representations, key=lambda r: int(r["bandwidth"]))
    elif min_bitrate and min_bitrate.isdigit():
        eligible = [
            r for r in representations
            if int(r["bandwidth"]) // 1000 >= min_bitrate
        ]
        if not eligible:
            rep = max(representations, key=lambda r: int(r["bandwidth"]))
        else:
            rep = min(eligible, key=lambda r: int(r["bandwidth"]))
    else:
        result = selector.select()

        if not result:
            print("no track selection made.")
            return
        
        rep = next(
            r for r in selector.representations
            if r["base_url"] == result["base_url"]
        )

    track_number = track_metadatav1.track_number
    track_name = track_metadatav1.track_name
    disc_number = track_metadatav1.disc

    safe_artist_name = safe_filename(track_metadatav1.artist_name, False)
    safe_album_name = safe_filename(track_metadatav1.album_name, False)

    track_output_dir = output_dir / safe_artist_name / safe_album_name

    output_filename = build_output_filename(disc_number, track_number, track_name)
    track_output_dir.mkdir(parents=True, exist_ok=True)
    output_file = track_output_dir / (output_filename + ".mp4")
    
    # create a temporary directory we can work with
    temp_dir = (output_dir / ".downloader")
    temp_dir.mkdir(parents=True, exist_ok=True)

    print("downloading encrypted file...")
    encrypted_file = Path(temp_dir / "encrypted.mp4")
    selector.download_full_file(rep, encrypted_file)

    print("fetching content keys...")
    content_key = Keys.getContentKeys(rep["pssh"], config, cookie_header)

    print("decrypting via mp4decrypt...")
    temp_output = Path(temp_dir / "decrypted_temp.mp4")

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

    print("processing metadata...")
    json_lyrics = Metadata2.fetch_lyrics(
        track_asin=track_metadatav2.asin,
        duration=track_metadatav2.duration_seconds,
        config=config
    )

    lyrics_obj = Lyrics.from_json(json_lyrics)
    track_metadatav2.attach_lyrics(lyrics_obj)

    artwork_url = Metadata2.fetch_artwork_v2(track_asin, config)

    with download_temp_artwork(artwork_url) as artwork_path:
        embed_metadata_and_cover(
            mp4_path=temp_output,
            track_metadatav1=track_metadatav1,
            track_metadatav2=track_metadatav2,
            album_metadatav2=album_metadatav2,
            artwork_path=artwork_path
        )

    temp_output.rename(output_file)
    shutil.rmtree(temp_dir)

    if track_metadatav2.lyrics and track_metadatav2.lyrics.has_content():
        track_metadatav2.lyrics.save_lrc(track_output_dir / (output_filename + ".lrc"))

    print(f"finished, saved to: {output_file}")

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

    print("fetching configs...")
    config = Configs.fetch_configs(cookie_header)

    print("fetching base metadata...")
    metadatav1 = Metadata.getMetadataFromEmbedLink(args.content_asin)

    output_dir = Path(args.output_dir) 

    print("fetching album metadata...")
    album_metadatav2 = Metadata2.get_album_metadatav2(
        album_asin=metadatav1.album_asin,
        config=config
    )

    if isinstance(metadatav1, AlbumMetadata):
        for index, track_metadatav1 in enumerate(metadatav1.tracks):
            fetch_track(
                track_asin=track_metadatav1.track_asin,
                track_metadatav1=track_metadatav1,
                track_metadatav2=album_metadatav2.tracks[index],
                album_metadatav2=album_metadatav2,
                output_dir=output_dir,
                config=config,
                cookie_header=cookie_header,
                min_bitrate=args.min_bitrate
            )
    elif isinstance(metadatav1, TrackMetadata):
        track_metadatav2 = next((t for t in album_metadatav2.tracks if t.asin == metadatav1.track_asin), None)
        if not track_metadatav2:
            raise ValueError(f"track {metadatav1.track_asin} not found in album {album_metadatav2.asin}")

        fetch_track(
            track_asin=args.content_asin,
            track_metadatav1=metadatav1,
            track_metadatav2=track_metadatav2,
            album_metadatav2=album_metadatav2,
            output_dir=output_dir,
            config=config,
            cookie_header=cookie_header,
            min_bitrate=args.min_bitrate
        )

if __name__ == "__main__":
    main()