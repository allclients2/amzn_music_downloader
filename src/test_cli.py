"""Offline smoke test for the CLI.

Drives the *real* `main.download()` flow and `Progress` bar without touching the
network: auth, metadata, manifest, lyrics and the download/decrypt/remux/tag
pipeline are all replaced with fakes. The simulated work walks through the same
step descriptions the real code emits, with short sleeps so the progress bar
animates the way it would for a genuine fetch.

Five modes, matching the branches of `main.run_download()`:

    python src/test_cli.py              # single track (default)
    python src/test_cli.py --album      # multi-track album
    python src/test_cli.py --playlist   # playlist (flat set of tracks)
    python src/test_cli.py --artist     # artist (sequence of albums)
    python src/test_cli.py --batch      # text file of mixed inputs (two-phase bar)
    python src/test_cli.py B07JZ7PW6F   # single track, your own ASIN
    python src/test_cli.py --album B07X # album, your own ASIN
"""

import asyncio
import os
import random
import sys
import tempfile

import main
from process import download
from metadata.metadata import AlbumMetadata, ArtistMetadata, PlaylistMetadata, TrackMetadata

# Made-up ASINs; nothing is ever sent anywhere.
_FAKE_TRACK_ASIN = "B0FAKETRACK"
_FAKE_ALBUM_ASIN = "B0FAKEALBUM"
_FAKE_PLAYLIST_ASIN = "B0FAKEPLST"
_FAKE_ARTIST_ASIN = "B0FAKEARTS"

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


def _fake_track_metadata(session, asin, defer_track_cover=False, type_hint=None):
    """Return ('track', TrackMetadata) exactly like metadata.fetch_metadata."""
    track = _make_track(asin, "Fake Track Marquee Example Fake Track Marquee Example")
    return "track", track


def _fake_album_metadata(session, asin, defer_track_cover=False, type_hint=None):
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


def _fake_playlist_metadata(session, asin, defer_track_cover=False, type_hint=None):
    """Return ('playlist', PlaylistMetadata) exactly like metadata.fetch_metadata."""
    n = len(_ALBUM_TRACKS)
    tracks = [
        _make_track(f"{asin}T{i:02d}", title, track_number=i, total_tracks=n)
        for i, title in enumerate(_ALBUM_TRACKS, start=1)
    ]
    return "playlist", PlaylistMetadata(
        name="Fake Playlist: A Marquee Example Mix", asin=asin, tracks=tracks
    )


def _fake_artist_metadata(session, asin, defer_track_cover=False, type_hint=None):
    """Dispatch like metadata.fetch_metadata: the artist ASIN resolves to a list of
    album ASINs, and each of those resolves to a full album (download() recurses)."""
    if asin == _FAKE_ARTIST_ASIN:
        return "artist", ArtistMetadata(
            name="Fake Artist",
            asin=asin,
            album_asins=[f"{_FAKE_ALBUM_ASIN}1", f"{_FAKE_ALBUM_ASIN}2"],
        )
    return _fake_album_metadata(session, asin)


def _fake_batch_metadata(session, asin, defer_track_cover=False, type_hint=None):
    """Dispatch a batch file's mixed inputs the way metadata.fetch_metadata would:
    each input id resolves to its own kind (track / album / playlist / artist), and
    the artist's album ASINs resolve to full albums."""
    if asin == _FAKE_ARTIST_ASIN:
        return _fake_artist_metadata(session, asin)
    if asin == _FAKE_PLAYLIST_ASIN:
        return _fake_playlist_metadata(session, asin)
    if asin == _FAKE_TRACK_ASIN:
        return _fake_track_metadata(session, asin)
    return _fake_album_metadata(session, asin)


def _fake_representations(session, asin, quality=None):
    """Return a parsed-manifest-shaped list (matches mpd_info.parse_mpd output)."""
    return [
        {
            "id": "1",
            "track_type": "HD",   # umbrella tier as Amazon labels it (CD-quality FLAC)
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
                              on_step=None, wvd_path="device.wvd",
                              resolve_hi_res_cover=False):
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
    playlist = "--playlist" in argv
    artist = "--artist" in argv
    batch = "--batch" in argv
    argv = [a for a in argv if a not in ("--album", "--playlist", "--artist", "--batch")]

    if batch:
        default_asin, fake_metadata = None, _fake_batch_metadata
    elif artist:
        default_asin, fake_metadata = _FAKE_ARTIST_ASIN, _fake_artist_metadata
    elif playlist:
        default_asin, fake_metadata = _FAKE_PLAYLIST_ASIN, _fake_playlist_metadata
    elif album:
        default_asin, fake_metadata = _FAKE_ALBUM_ASIN, _fake_album_metadata
    else:
        default_asin, fake_metadata = _FAKE_TRACK_ASIN, _fake_track_metadata

    # Patch every network/IO boundary so the real download()/download_batch()
    # orchestration and Progress rendering run for real. Auth is resolved in `main`
    # (run_download); the metadata/manifest/track boundaries live in `download`.
    main.auth.get_session = lambda *a, **k: FakeSession()
    download.fetch_metadata = fake_metadata
    download.fetch_representations = _fake_representations
    download.process_track = _fake_process_track
    download.fetch_track = _fake_fetch_track

    # The batch path needs a real text file of mixed inputs so links.resolve_inputs
    # reads it and run_download routes to the two-phase batch bar.
    batch_file = None
    if batch:
        fd, batch_file = tempfile.mkstemp(prefix="batch-", suffix=".txt")
        with os.fdopen(fd, "w") as f:
            f.write("\n".join((
                "# fake batch of mixed inputs",
                _FAKE_TRACK_ASIN,
                _FAKE_ALBUM_ASIN,
                _FAKE_PLAYLIST_ASIN,
                _FAKE_ARTIST_ASIN,
            )) + "\n")

    arg = argv[0] if argv else (batch_file or default_asin)
    sys.argv = ["test_cli.py", arg, "--quality", "HD"]
    try:
        main.main()
    finally:
        if batch_file:
            os.remove(batch_file)


if __name__ == "__main__":
    main_test()
