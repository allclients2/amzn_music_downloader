import json;
import sys;
from pathlib import Path;

from metadata2 import Metadata2;   # adjust if your file name differs
from media_utils import MediaUtils;  # if you placed ffprobe helper there


def main():
    if len(sys.argv) < 3:
        print("usage: python test_lyrics.py <track_asin> <config.json>");
        sys.exit(1);

    track_asin = sys.argv[1]
    config_path = Path(sys.argv[2])

    if not config_path.exists():
        print("config file not found.");
        sys.exit(1);

    # Load config
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f);


    print("fetching lyrics...");
    response = Metadata2.fetch_idk(
        track_asin=track_asin,
        config=config
    );

    output_file = "idk.json";

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(response);

    print(f"json saved to: {output_file}");


if __name__ == "__main__":
    main();