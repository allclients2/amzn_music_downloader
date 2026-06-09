"""Download, decrypt, remux to FLAC, and tag a single track.

Pipeline: signed manifest -> download encrypted file from BaseURL -> Widevine
content key (signed license) -> mp4decrypt -> ffmpeg remux to .flac (lossless
copy of the FLAC stream) -> mutagen FLAC tags + embedded cover -> sidecar .lrc.

Files land at `<output_dir>/<album_artist>/<album>/<disc> - <track> <title>.flac`;
the `<album_artist>/<album>/` folders are skipped when `build_folder_structure`
is False (the bot writes flat into a per-request directory).
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import requests
from mutagen.flac import FLAC, Picture

_log = logging.getLogger("downloader.track")

from keys import Keys
from lyrics import Lyrics
from metadata import TrackMetadata
from mpd_info import find_representation
from util import safe_filename, build_output_filename

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
# FLAC Picture type 3 = front cover.
_FRONT_COVER = 3

# Shared scratch directory (under the output dir) for per-track encrypted /
# decrypted / remuxed files; each track works in its own subdir inside it.
_TEMP_SUBDIR = ".downloader"


def purge_temp_dir(output_dir: Path) -> None:
    """Remove the shared scratch directory after a download batch finishes.

    Each track already deletes its own subdir; this clears the empty parent that
    would otherwise be left behind. Safe to call when it doesn't exist.
    """
    shutil.rmtree(Path(output_dir) / _TEMP_SUBDIR, ignore_errors=True)


@contextmanager
def download_temp_artwork(url: str, directory: str):
    if not url:
        yield None
        return

    tmp_path = None
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg", dir=directory) as tmp:
        response = requests.get(
            url,
            timeout=10,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "User-Agent": _UA,
            },
        )
        response.raise_for_status()
        tmp.write(response.content)
        tmp_path = tmp.name

    try:
        yield tmp_path
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def download_full_file(base_url: str, output_path):
    r = requests.get(base_url, stream=True)
    if r.status_code != 200:
        _log.error("download failed. Status code: %s", r.status_code)
        return None
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return output_path


def remux_to_flac(src_mp4: Path, dst_flac: Path, codec: str) -> None:
    """Remux the decrypted (fragmented) MP4 to a .flac container.

    Amazon HD/UHD audio is FLAC, so we copy the stream losslessly. For any other
    codec we re-encode to FLAC so a .flac file is still produced.
    """
    lossless = (codec or "").lower().startswith("flac")
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-i", str(src_mp4),
        "-map", "0:a",
        "-c:a", "copy" if lossless else "flac",
        str(dst_flac),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 and lossless:
        # Fall back to a re-encode if the stream couldn't be copied.
        cmd[cmd.index("copy")] = "flac"
        result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg remux failed:\n{result.stderr}")


def _tag_track(flac_path: str, track: TrackMetadata, lyrics, temp_dir: str):
    """Download the cover into `temp_dir` and embed tags + art (blocking)."""
    with download_temp_artwork(track.cover_url, temp_dir) as artwork_path:
        embed_metadata_and_cover(flac_path, track, lyrics, artwork_path)


def embed_metadata_and_cover(flac_path: str, track: TrackMetadata, lyrics, artwork_path):
    audio = FLAC(flac_path)
    audio.delete()  # clear any tags carried over from the source container

    def setv(key, value):
        if value is not None and value != "":
            audio[key] = str(value)

    setv("TITLE", track.title)
    setv("ARTIST", track.artist)
    setv("ALBUM", track.album_name)
    setv("ALBUMARTIST", track.album_artist)
    setv("TRACKNUMBER", track.track_number)
    setv("TRACKTOTAL", track.total_tracks)
    setv("DISCNUMBER", track.disc)
    setv("DISCTOTAL", track.total_discs)
    setv("DATE", track.release_date)
    setv("COPYRIGHT", track.copyright)
    setv("LABEL", track.label)
    setv("GENRE", track.genre)
    setv("ISRC", track.isrc)
    setv("COMPOSER", track.composers)
    setv("EXPLICIT", "1" if track.is_explicit else "0")
    if track.popularity is not None:
        setv("POPULARITY", track.popularity)

    if lyrics and lyrics.has_content():
        setv("LYRICS", lyrics.to_mp4_lyrics())

    if artwork_path:
        with open(artwork_path, "rb") as img:
            cover_data = img.read()
        pic = Picture()
        pic.type = _FRONT_COVER
        pic.mime = "image/jpeg"
        pic.desc = "Cover"
        pic.data = cover_data
        audio.add_picture(pic)

    audio.save()


async def process_track(
    session,
    track: TrackMetadata,
    representation,
    output_dir: Path,
    build_folder_structure: bool = True,
    lyrics_resp=None,
    on_step=None,
    wvd_path: str = "device.wvd",
):
    """Download + decrypt + remux + tag, given an already-selected representation.

    `lyrics_resp` may be pre-fetched (single-track fast path); when None it is
    fetched here in parallel with the download + license. `on_step(desc)` is an
    optional callback invoked at the start of each stage (drives the progress bar).
    """
    def step(desc):
        _log.debug(desc)
        if on_step:
            on_step(desc)

    if representation is None:
        _log.warning("no playable representation for %s; skipping.", track.title)
        return

    rep = representation.mpd_representation

    if build_folder_structure:
        safe_album_artist_name = safe_filename(track.album_artist, False)
        safe_album_name = safe_filename(track.album_name, False)
        track_output_dir = output_dir / safe_album_artist_name / safe_album_name
    else:
        track_output_dir = output_dir

    output_filename = build_output_filename(track.disc, track.track_number, track.title)
    track_output_dir.mkdir(parents=True, exist_ok=True)
    output_file = track_output_dir / (output_filename + ".flac")

    if os.path.exists(output_file):
        _log.info("file %s already exists; skipping.", output_filename)
        return

    # Per-track temp dir so concurrent downloads don't clobber each other's
    # encrypted/decrypted/remuxed scratch files.
    base_temp = output_dir / _TEMP_SUBDIR
    base_temp.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="dl-", dir=base_temp))
    encrypted_file = temp_dir / "encrypted.mp4"

    step("downloading track")
    coros = [
        asyncio.to_thread(Keys.getContentKeys, session, track.asin, rep["pssh"], wvd_path),
        asyncio.to_thread(download_full_file, rep["base_url"], encrypted_file),
    ]
    fetch_lyrics = lyrics_resp is None
    if fetch_lyrics:
        coros.append(asyncio.to_thread(session.get_track_lyrics, track.asin))
    results = await asyncio.gather(*coros)
    content_key = results[0]
    if fetch_lyrics:
        lyrics_resp = results[2]

    # The decrypt/remux/tag stages are blocking (subprocess + requests + mutagen);
    # run them off the event loop so other concurrent tracks keep making progress.
    step("decrypting")
    decrypted_mp4 = temp_dir / "decrypted_temp.mp4"
    try:
        await asyncio.to_thread(
            subprocess.run,
            ["mp4decrypt", "--key", content_key, str(encrypted_file), str(decrypted_mp4)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        _log.error("decryption failed for %s", track.title)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return

    step("remuxing to flac")
    flac_temp = temp_dir / "decoded_temp.flac"
    await asyncio.to_thread(remux_to_flac, decrypted_mp4, flac_temp, rep.get("codec"))

    step("tagging metadata")
    lyrics_obj = Lyrics.from_xray(lyrics_resp)
    await asyncio.to_thread(_tag_track, str(flac_temp), track, lyrics_obj, str(temp_dir))

    flac_temp.rename(output_file)
    shutil.rmtree(temp_dir, ignore_errors=True)

    if lyrics_obj.has_content() and lyrics_obj.to_lrc():
        lyrics_obj.save_lrc(track_output_dir / (output_filename + ".lrc"))

    _log.info("saved to: %s", output_file)


async def fetch_track(
    session,
    track: TrackMetadata,
    output_dir: Path,
    quality,
    build_folder_structure: bool = True,
    on_step=None,
    wvd_path: str = "device.wvd",
):
    """Album/general path: fetch the manifest, select a stream, then process."""
    if on_step:
        on_step("fetching manifest")
    representation = await asyncio.to_thread(find_representation, session, track.asin, quality)
    await process_track(
        session, track, representation, output_dir, build_folder_structure,
        on_step=on_step, wvd_path=wvd_path,
    )
