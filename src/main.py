from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH
import requests
import base64
from pathlib import Path;
from configs import Configs;
from cookies import Cookies
from keys import Keys;
from mpd_info import MpdInfo;
import argparse
from mpd_selector import MPDStreamSelector;
import subprocess;
import os;


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("content_asin", help="The ASIN of the track; which is it's primary identifier")
    parser.add_argument("output_file", default="output.mp4")
    parser.add_argument("--cookies_file", default="cookies.txt")
    args = parser.parse_args()

    cookieHeader = Cookies.netscape_to_cookie_header(args.cookies_file)
    config = Configs.fetch_configs(cookieHeader)

    print("fetching track MPD Streams...")
    manifestXML = MpdInfo.getTrackInfo(args.content_asin, config, cookieHeader)
    selector = MPDStreamSelector(manifestXML)

    result = selector.select()

    if result:
        print(f"downloading track from: {result["base_url"]}...")

        rep = next(
            r for r in selector.representations
            if r["base_url"] == result["base_url"]
        );

        output_file = args.output_file

        print(f"fetching content keys with PSSH: {result["pssh"]}...")
        contentKey: str = Keys.getContentKeys(result["pssh"], config, cookieHeader)
        selector.download_full_file(rep, "encrypted.mp4")
    

        print(f"decrypting file with kid and key: {contentKey}...")
        cmd = [
            "mp4decrypt",
            "--key", contentKey,
            "encrypted.mp4",
            output_file
        ]

        subprocess.run(cmd, check=True)

        print("removing encrypted temporary file...")
        os.remove("encrypted.mp4")
        print(f"finished! wrote track to {output_file}")
    else:
        print("no tack selection made")


if __name__ == "__main__":
    main()