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
        print("usage: python test_album.py <album_asin> <config.json>")
        sys.exit(1)

    album_asin = sys.argv[1]
    config_path = Path(sys.argv[2])

    if not config_path.exists():
        print("config file not found.")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    print("fetching album metadata...")
    album = Metadata2.get_album_metadatav2(
        album_asin=album_asin,
        config=config
    )

    print("verifying object type...")
    assert_true(isinstance(album, AlbumMetadata), "return type is not AlbumMetadata")

    print("verifying album fields...")

    assert_true(album.asin == album_asin, "album ASIN mismatch")
    assert_true(album.name is not None and len(album.name) > 0, "album name missing")
    assert_true(album.artist is not None and len(album.artist) > 0, "album artist missing")
    assert_true(album.track_count > 0, "track count invalid")
    assert_true(len(album.tracks) > 0, "to tracks parsed")
    assert_true(album.total_duration_seconds > 0, "album duration invalid")

    print("verifying tracks...")

    for index, track in enumerate(album.tracks):
        print(f"{index} - {track.title}")


        assert_true(isinstance(track, TrackMetadata), f"track {index} wrong type")
        assert_true(track.asin is not None, f"track {index} ASIN missing")
        assert_true(track.title is not None and len(track.title) > 0, f"track {index} title missing")
        assert_true(track.duration_seconds > 0, f"track {index} duration invalid")
        assert_true(track.artist == album.artist, f"track {index} artist mismatch")


    print("verifying track ordering consistency...")
    assert_true(
        len(album.tracks) == album.track_count,
        "track count does not match parsed tracks?"
    )

    print("verifying duration sanity check...")

    print("track sum:", album.total_duration_seconds)

    print("\nall verification checks passed.")
    print(f"\nalbum: {album.name}")
    print(f"artist: {album.artist}")
    print(f"tracks: {album.track_count}")
    print(f"release Date: {album.release_date_iso}")
    print(f"total Duration: {album.total_duration_seconds} seconds")


if __name__ == "__main__":
    main()