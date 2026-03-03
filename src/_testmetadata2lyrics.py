import sys
import json
from pathlib import Path
from metadata2 import Metadata2
from metadata_objects import AlbumMetadata, TrackMetadata


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    if len(sys.argv) < 3:
        print("usage: python test_lyrics.py <track_asin> <config.json>")
        sys.exit(1)

    track_asin = sys.argv[1]
    config_path = Path(sys.argv[2])

    if not config_path.exists():
        print("config file not found.")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    print("fetching album metadata...")
    lyrics_json = Metadata2.fetch_lyrics(
        track_asin=track_asin,
        duration=265,
        config=config
    )

    print("lyrics json (truncated):", lyrics_json[:250])


if __name__ == "__main__":
    main()