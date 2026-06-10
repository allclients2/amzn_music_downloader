"""Download, decrypt, remux, and tag a single track.

Pipeline: signed manifest -> download encrypted file from BaseURL -> Widevine
content key (signed license) -> mp4decrypt -> ffmpeg remux -> mutagen tags +
embedded cover -> sidecar .lrc.

Every codec is stream-copied into its natural container (never transcoded), so
nothing is lost or pointlessly re-encoded: Amazon HD/UHD FLAC → `.flac`, the lossy
Opus LD/SD tiers → native `.opus`, and the spatial tiers (Dolby Atmos / Sony 360RA)
→ `.mp4` (E-AC-3 / MPEG-H, tagged via MP4 atoms) or a raw `.ac4` (Dolby AC-4,
untaggable). See `_output_spec`.

Files land at `<output_dir>/<album_artist>/<album>/<disc> - <track> <title>.<ext>`
(`.flac` / `.opus`, or `.mp4` / `.ac4` for spatial); the `<album_artist>/<album>/`
folders are skipped when `build_folder_structure` is False (the bot writes flat into
a per-request directory).
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
from mpd_info import find_representation, _AUDIO_EXTENSIONS
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

# Output container/tagging per source codec — always a stream copy, never a
# transcode: the source codec is kept in its natural container so nothing is lost or
# pointlessly re-encoded.
#   • FLAC (Amazon HD/UHD)        → .flac, Vorbis comments
#   • Opus (the lossy LD/SD tiers) → .opus, Vorbis comments (re-encoding lossy Opus to
#     FLAC would only bloat it and mislabel it — keep it native)
#   • Dolby Atmos/DD+ (E-AC-3, AC-3) and MPEG-H 360RA (MHA1/MHM1) → .mp4, MP4 tags
#     (the .m4a/ipod muxer rejects E-AC-3, so it must be .mp4)
#   • Dolby AC-4 → raw .ac4 elementary stream; no container, so it can't be tagged
# `tag_mode`: "flac" | "opus" | "mp4" | None (raw, untaggable).
def _output_spec(codec):
    """Return ``(extension, tag_mode)`` for a representation codec."""
    c = str(codec or "").lower()
    if c.startswith("opus"):
        return ".opus", "opus"
    if c.startswith(("ec-3", "ec3", "eac3", "ac-3", "ac3", "mha", "mhm")):
        return ".mp4", "mp4"
    if c.startswith(("ac-4", "ac4")):
        return ".ac4", None
    return ".flac", "flac"


def _existing_download(track_output_dir: Path, output_filename: str):
    """The already-downloaded file for this track in any audio extension, or None."""
    for ext in _AUDIO_EXTENSIONS:
        candidate = track_output_dir / (output_filename + ext)
        if candidate.exists():
            return candidate
    return None


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


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


def remux_copy(src_mp4: Path, dst: Path) -> None:
    """Stream-copy the decrypted audio into `dst` (the extension picks the muxer).

    Every codec goes through here — FLAC, Opus, and the spatial formats are all
    preserved untouched (`-c:a copy`, no transcode). A container that can't carry the
    stream raises rather than silently re-encoding.
    """
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-i", str(src_mp4),
        "-map", "0:a",
        "-c:a", "copy",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg remux failed:\n{result.stderr}")


def _tag_track(media_path: str, track: TrackMetadata, lyrics, temp_dir: str,
               tag_mode: str = "flac"):
    """Download the cover into `temp_dir` and embed tags + art (blocking).

    `tag_mode`: "flac"/"opus" (Vorbis comments), "mp4" (MP4 atoms, for the spatial
    .mp4 output), or None — a raw elementary stream (AC-4 .ac4) that carries no
    container, so there's nothing to tag."""
    if tag_mode is None:
        return
    with download_temp_artwork(track.cover_url, temp_dir) as artwork_path:
        if tag_mode == "mp4":
            embed_metadata_and_cover_mp4(media_path, track, lyrics, artwork_path)
        elif tag_mode == "opus":
            embed_metadata_and_cover_opus(media_path, track, lyrics, artwork_path)
        else:
            embed_metadata_and_cover(media_path, track, lyrics, artwork_path)


def embed_metadata_and_cover_mp4(mp4_path: str, track: TrackMetadata, lyrics, artwork_path):
    """Tag a spatial .mp4 (Atmos / 360RA) with the same fields as the FLAC path,
    mapped onto MP4 atoms (`mutagen.mp4`)."""
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(mp4_path)
    audio.delete()  # clear any tags carried over from the source container

    def setv(key, value):
        if value is not None and value != "":
            audio[key] = [str(value)]

    setv("\xa9nam", track.title)
    setv("\xa9ART", track.artist)
    setv("\xa9alb", track.album_name)
    setv("aART", track.album_artist)
    setv("\xa9day", track.release_date)
    setv("cprt", track.copyright)
    setv("\xa9gen", track.genre)
    setv("\xa9wrt", track.composers)

    if track.track_number:
        audio["trkn"] = [(_as_int(track.track_number), _as_int(track.total_tracks))]
    if track.disc:
        audio["disk"] = [(_as_int(track.disc), _as_int(track.total_discs))]

    # Freeform atoms for fields without a standard MP4 key.
    def freeform(name, value):
        if value is not None and value != "":
            audio[f"----:com.apple.iTunes:{name}"] = [str(value).encode("utf-8")]

    freeform("ISRC", track.isrc)
    freeform("LABEL", track.label)
    freeform("EXPLICIT", "1" if track.is_explicit else "0")
    if track.popularity is not None:
        freeform("POPULARITY", track.popularity)

    if lyrics and lyrics.has_content():
        setv("\xa9lyr", lyrics.to_mp4_lyrics())

    if artwork_path:
        with open(artwork_path, "rb") as img:
            audio["covr"] = [MP4Cover(img.read(), imageformat=MP4Cover.FORMAT_JPEG)]

    audio.save()


def _set_vorbis_fields(audio, track: TrackMetadata, lyrics):
    """Populate Vorbis comments shared by FLAC and Ogg Opus (same key vocabulary)."""
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


def _build_cover_picture(artwork_path) -> Picture:
    """A FLAC `Picture` (front cover) — used directly by FLAC, base64-wrapped by Opus."""
    with open(artwork_path, "rb") as img:
        cover_data = img.read()
    pic = Picture()
    pic.type = _FRONT_COVER
    pic.mime = "image/jpeg"
    pic.desc = "Cover"
    pic.data = cover_data
    return pic


def embed_metadata_and_cover(flac_path: str, track: TrackMetadata, lyrics, artwork_path):
    audio = FLAC(flac_path)
    audio.delete()  # clear any tags carried over from the source container
    _set_vorbis_fields(audio, track, lyrics)
    if artwork_path:
        audio.add_picture(_build_cover_picture(artwork_path))
    audio.save()


def embed_metadata_and_cover_opus(opus_path: str, track: TrackMetadata, lyrics, artwork_path):
    """Tag a native Ogg Opus (lossy LD/SD) file — same Vorbis fields as FLAC, with
    the cover carried in the standard base64 METADATA_BLOCK_PICTURE comment."""
    import base64
    from mutagen.oggopus import OggOpus

    audio = OggOpus(opus_path)
    audio.delete()
    _set_vorbis_fields(audio, track, lyrics)
    if artwork_path:
        pic = _build_cover_picture(artwork_path)
        audio["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
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
    # Each codec keeps its native container — stream-copied, never transcoded.
    extension, tag_mode = _output_spec(rep.get("codec"))

    if build_folder_structure:
        safe_album_artist_name = safe_filename(track.album_artist, False)
        safe_album_name = safe_filename(track.album_name, False)
        track_output_dir = output_dir / safe_album_artist_name / safe_album_name
    else:
        track_output_dir = output_dir

    output_filename = build_output_filename(track.disc, track.track_number, track.title)
    output_file = track_output_dir / (output_filename + extension)

    # Skip if the track is already downloaded in *any* format, not just the one this
    # run would produce (e.g. a prior FLAC when now asking for Opus).
    existing = _existing_download(track_output_dir, output_filename)
    if existing is not None:
        _log.info("file %s already exists (%s); skipping.", output_filename, existing.suffix)
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

    step(f"remuxing to {extension.lstrip('.')}")
    media_temp = temp_dir / ("decoded_temp" + extension)
    await asyncio.to_thread(remux_copy, decrypted_mp4, media_temp)

    step("tagging metadata")
    lyrics_obj = Lyrics.from_xray(lyrics_resp)
    await asyncio.to_thread(_tag_track, str(media_temp), track, lyrics_obj, str(temp_dir), tag_mode)

    track_output_dir.mkdir(parents=True, exist_ok=True)
    media_temp.rename(output_file)
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
