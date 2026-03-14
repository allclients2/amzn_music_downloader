import argparse
import asyncio

from pathlib import Path
from configs import fetch_configs, build_browser_with_cookies
from cookies import Cookies
from metadata import Metadata, TrackMetadata, AlbumMetadata
from cookies import Cookies, CookieError
from metadata2 import Metadata2
from fetch_track import fetch_track
from mpd_info import find_representation
from http.cookiejar import MozillaCookieJar
import requests
import os

def parse_args():
    parser = argparse.ArgumentParser(
        description="Download specified track"
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

def cookie_header_to_jar(session, cookie_header, domain=".amazon.co.jp"):
    for pair in cookie_header.split(";"):
        name, value = pair.strip().split("=", 1)
        session.cookies.set(name, value, domain=domain, path="/")

def load_cookie_session(cookie_file):
    session = requests.Session()

    jar = MozillaCookieJar(cookie_file)

    if os.path.exists(cookie_file):
        jar.load(ignore_discard=True, ignore_expires=True)

    session.cookies = jar
    return session, jar

async def main():
    args = parse_args()

    try:
        if args.from_browser:
            print("loading cookies from browser...")
            cookie_header = Cookies.from_browser(
                domain=".amazon.co.jp",
                browser=args.browser
            )

            session = requests.Session()
            cookie_header_to_jar(session, cookie_header)
            jar = None
        else:
            session, jar = load_cookie_session(args.cookies_file)

    except CookieError as e:
        print(str(e))
        return

    browser = build_browser_with_cookies(session)

    print("fetching configs...")
    config = fetch_configs(session)

    if jar:
       print("updated cookies")
       jar.save(ignore_discard=False, ignore_expires=False)
    
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
            representation = find_representation(
                track_asin=track_metadatav1.track_asin,
                config=config,
                cookie_header=cookie_header,
                min_bitrate=args.min_bitrate
            )
            await fetch_track(
                track_representation=representation,
                track_metadatav1=track_metadatav1,
                track_metadatav2=album_metadatav2.tracks[index],
                album_metadatav1=metadatav1,
                album_metadatav2=album_metadatav2,
                output_dir=output_dir,
                config=config,
                cookie_header=cookie_header,
            )
    elif isinstance(metadatav1, TrackMetadata):
        track_metadatav2 = next((t for t in album_metadatav2.tracks if t.asin == metadatav1.track_asin), None)
        if not track_metadatav2:
            raise ValueError(f"track {metadatav1.track_asin} not found in album {album_metadatav2.asin}")

        album_metadatav1 = metadatav1.fetch_disc_info()

        representation = find_representation(
            track_asin=args.content_asin,
            config=config,
            cookie_header=cookie_header,
            min_bitrate=args.min_bitrate
        )
        await fetch_track(
            track_representation=representation,
            track_metadatav1=metadatav1,
            track_metadatav2=track_metadatav2,
            album_metadatav1=album_metadatav1,
            album_metadatav2=album_metadatav2,
            output_dir=output_dir,
            config=config,
            cookie_header=cookie_header,
        )

if __name__ == "__main__":
    asyncio.run(main())