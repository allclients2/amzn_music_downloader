import argparse
import requests
import os
import re
import subprocess
from pathlib import Path

from configs import Configs
from cookies import Cookies
from keys import Keys
from metadata import Metadata
from mpd_info import MpdInfo
from mpd_selector import MPDStreamSelector
from cookies import Cookies, CookieError
from mutagen.mp4 import MP4, MP4Cover


def sanitize_filename(name: str) -> str:
    """Make filename OS safe."""
    return re.sub(r'[<>:"/\\|?*]', "", name)


def build_output_filename(track_name: str, artist_name: str) -> str:
    filename = f"{track_name} - {artist_name}.mp4"
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

def embed_metadata_and_cover(mp4_path: str, image_path: str, metadata: dict):
    audio = MP4(mp4_path)

    with open(image_path, "rb") as img:
        cover = img.read()

    audio["\xa9nam"] = metadata["track_name"]
    audio["\xa9ART"] = metadata["artist_name"]
    audio["\xa9alb"] = metadata["album_title"]
    audio["covr"] = [
        MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)
    ]
    audio.save()

def main():
    args = parse_args()

    try:
        if args.from_browser:
            print("Loading cookies from browser...")
            cookie_header = Cookies.from_browser(
                domain="amazon.co.jp",
                browser=args.browser
            )
        else:
            cookie_header = Cookies.netscape_to_cookie_header(args.cookies_file)
    except CookieError as e:
        print(str(e))
        return
    
    config = Configs.fetch_configs(cookie_header)

    print("Fetching track MPD streams...")
    manifest_xml = MpdInfo.getTrackInfo(args.content_asin, config, cookie_header)
    selector = MPDStreamSelector(manifest_xml)

    result = selector.select()

    if not result:
        print("No track selection made.")
        return

    if args.verbose:
        print(f"Selected stream: {result}")

    rep = next(
        r for r in selector.representations
        if r["base_url"] == result["base_url"]
    )

    print("Fetching metadata...")
    metadata = Metadata.getTrackMetadataFromEmbedLink(args.content_asin)

    track_name = metadata["track_name"]
    artist_name = metadata["artist_name"]
    artwork_url = metadata["artwork_url"]

    # Determine output file
    if args.output:
        output_file = Path(args.output)
    else:
        filename = build_output_filename(track_name, artist_name)
        output_file = Path(args.output_dir) / filename

    print(f"Output file: {output_file}")

    print("Fetching content keys...")
    content_key = Keys.getContentKeys(result["pssh"], config, cookie_header)

    encrypted_file = Path("encrypted.mp4")

    print("Downloading encrypted file...")
    selector.download_full_file(rep, encrypted_file)

    print("Decrypting...")
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
        print("Decryption failed.")
        return
    
    # Download artwork then embed it along with metadata
    artwork_path = "cover.jpg"
    download_artwork(artwork_url, artwork_path)

    embed_metadata_and_cover(temp_output, artwork_path, metadata)

    os.remove(artwork_path)

    temp_output.rename(unicode_output)

    if not args.keep_encrypted:
        encrypted_file.unlink(missing_ok=True)

    print(f"Finished! Saved to: {output_file}")


if __name__ == "__main__":
    main()