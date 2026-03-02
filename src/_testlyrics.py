import json;
import sys;
from pathlib import Path;

from lyrics import Lyrics;   # adjust if your file name differs
from media_utils import MediaUtils;  # if you placed ffprobe helper there


def main():
    if len(sys.argv) < 3:
        print("Usage: python test_lyrics.py <track_asin> <config.json>");
        sys.exit(1);

    track_asin = sys.argv[1]
    config_path = Path(sys.argv[2])

    if not config_path.exists():
        print("Config file not found.");
        sys.exit(1);

    # Load config
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f);


    print("Fetching lyrics...");
    lyrics_json = Lyrics.fetch_lyrics(
        track_asin=track_asin,
        duration=265,
        config=config
    );

    print("Converting to LRC...");
    lrc_content = Lyrics.convert_lyrics_to_lrc(lyrics_json);

    output_file = "idk.lrc";

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(lrc_content);

    print(f"LRC saved to: {output_file}");
    print("Done.");


if __name__ == "__main__":
    main();