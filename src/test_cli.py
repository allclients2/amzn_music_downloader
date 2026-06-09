"""Offline smoke test for the CLI.

Drives the *real* `main.download()` flow and `Progress` bar without touching the
network: auth, metadata, manifest, lyrics and the download/decrypt/remux/tag
pipeline are all replaced with fakes. The simulated work walks through the same
step descriptions the real code emits, with short sleeps so the progress bar
animates the way it would for a genuine fetch.

Two modes, matching the two branches of `main.download()`:

    python src/test_cli.py              # single track (default)
    python src/test_cli.py --album      # multi-track album
    python src/test_cli.py B07JZ7PW6F   # single track, your own ASIN
    python src/test_cli.py --album B07X # album, your own ASIN
"""

import asyncio
import random
import sys

import main
from metadata import AlbumMetadata, TrackMetadata

# Made-up ASINs; nothing is ever sent anywhere.
_FAKE_TRACK_ASIN = "B0FAKETRACK"
_FAKE_ALBUM_ASIN = "B0FAKEALBUM"

# Mirrors the stage descriptions emitted by the real fetch_track.process_track,
# paired with how long to pretend each stage takes (seconds).
_FAKE_STEPS = [
    ("downloading track", 0.8),
    ("decrypting", 0.4),
    ("remuxing to flac", 0.4),
    ("tagging metadata", 0.3),
]

# The album path additionally fetches the manifest first (see fetch_track).
_FAKE_ALBUM_STEPS = [("fetching manifest", 0.3)] + _FAKE_STEPS

# Track titles for the fake album (varied lengths to exercise the slot lines).
_ALBUM_TRACKS = [
    "Opening Theme",
    "A Considerably Longer Track Title That Runs On",
    "Interlude",
    "Midpoint",
    "Another Fairly Long Song Name For Good Measure",
    "Short One",
    "Penultimate",
    "Bridge",
    "Reprise",
    "Hidden Track",
    "Encore",
    "Closing Credits",
]


class FakeSession:
    """Stand-in for the signed AmazonMusicMobileAPI session."""

    def get_track_lyrics(self, asin):
        return None


def _make_track(asin, title, track_number=1, total_tracks=1, disc=1, total_discs=1):
    return TrackMetadata(
        asin=asin,
        title=title,
        artist="Fake Artist",
        album_name="Fake Album",
        album_artist="Fake Artist",
        album_asin=_FAKE_ALBUM_ASIN,
        disc=disc,
        track_number=track_number,
        total_tracks=total_tracks,
        total_discs=total_discs,
        duration_seconds=212,
        is_explicit=False,
        isrc="USFAKE0000001",
        release_date="2024-01-01",
        copyright="℗ 2024 Fake Records",
        label="Fake Records",
        genre="Test",
    )


def _fake_track_metadata(session, asin):
    """Return ('track', TrackMetadata) exactly like metadata.fetch_metadata."""
    track = _make_track(asin, "Fake Track Marquee Example Fake Track Marquee Example")
    return "track", track


def _fake_album_metadata(session, asin):
    """Return ('album', AlbumMetadata) exactly like metadata.fetch_metadata."""
    n = len(_ALBUM_TRACKS)
    tracks = [
        _make_track(f"{asin}T{i:02d}", title, track_number=i, total_tracks=n)
        for i, title in enumerate(_ALBUM_TRACKS, start=1)
    ]
    album = AlbumMetadata(
        album_name="Fake Album: A Multi-Track Marquee Example Collection",
        artist_name="Fake Artist",
        album_asin=asin,
        cover_url=None,
        release_date="2024-01-01",
        copyright="℗ 2024 Fake Records",
        label="Fake Records",
        genre="Test",
        track_count=n,
        total_discs=1,
        tracks=tracks,
    )
    return "album", album


def _fake_representations(session, asin):
    """Return a parsed-manifest-shaped list (matches mpd_info.parse_mpd output)."""
    return [
        {
            "id": "1",
            "track_type": "AUDIO",
            "codec": "flac",
            "bandwidth": 940000,
            "sample_rate": "44100",
            "bit_depth": "16",
            "base_url": "https://example.invalid/fake.mp4",
            "first_segment_range": None,
            "pssh": "FAKEPSSH==",
        },
    ]


async def _fake_process_track(session, track, representation, output_dir,
                              build_folder_structure=True, lyrics_resp=None,
                              on_step=None, wvd_path="device.wvd"):
    """Simulate the single-track pipeline, driving the progress bar via on_step."""
    for desc, delay in _FAKE_STEPS:
        if on_step:
            on_step(desc)
        await asyncio.sleep(delay)


async def _fake_fetch_track(session, track, output_dir, quality,
                            build_folder_structure=True, on_step=None,
                            wvd_path="device.wvd"):
    """Simulate one album track, driving its slot's step bar via on_step."""
    for desc, delay in _FAKE_ALBUM_STEPS:
        if on_step:
            on_step(desc)
        # Jitter each stage so concurrent tracks drift out of lockstep (as real
        # network/decode timing would), giving the slot bars varied progress.
        await asyncio.sleep(delay * random.uniform(0.5, 1.6))


def main_test():
    argv = sys.argv[1:]
    album = "--album" in argv
    argv = [a for a in argv if a != "--album"]
    asin = argv[0] if argv else (_FAKE_ALBUM_ASIN if album else _FAKE_TRACK_ASIN)

    # Patch every network/IO boundary on the `main` module namespace so the real
    # download() orchestration and Progress rendering run for real, unmocked.
    main.auth.get_session = lambda *a, **k: FakeSession()
    main.fetch_metadata = _fake_album_metadata if album else _fake_track_metadata
    main.fetch_representations = _fake_representations
    main.process_track = _fake_process_track
    main.fetch_track = _fake_fetch_track

    sys.argv = ["test_cli.py", asin, "--default-quality", "HD"]
    asyncio.run(main.main())


if __name__ == "__main__":
    main_test()
