"""Download, decrypt, remux, and tag a single track.

Pipeline: signed manifest -> download encrypted file from BaseURL -> Widevine
content key (signed license) -> in-process CENC decrypt (`process.decrypt`) ->
ffmpeg remux -> mutagen tags + embedded cover -> sidecar .lrc.

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
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests

_log = logging.getLogger("downloader.track")

from process.keys import Keys
from process.decrypt import decrypt_mp4
from process.lyrics import Lyrics
from process.tagging import tag_track, download_artwork
from metadata.metadata import TrackMetadata, resolve_track_cover
from metadata.mpd_info import find_representation, _AUDIO_EXTENSIONS
from util import safe_filename, build_output_filename

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


def purge_temp_dir(output_dir: Path) -> None:
    """Remove the shared scratch directory after a download batch finishes.

    Each track already deletes its own subdir; this clears the empty parent that
    would otherwise be left behind. Safe to call when it doesn't exist.
    """
    shutil.rmtree(Path(output_dir) / _TEMP_SUBDIR, ignore_errors=True)


# Streaming copy buffer. The audio bodies are ~100 MB (UHD) served as
# `application/octet-stream` (no transfer decoding), so a large buffer copies them
# in ~100 raw passes instead of ~12k tiny ones — far less Python/GIL overhead with
# several downloads streaming through worker threads at once.
_DOWNLOAD_CHUNK = 1024 * 1024  # 1 MiB


def download_full_file(base_url: str, output_path):
    r = requests.get(base_url, stream=True)
    if r.status_code != 200:
        _log.error("download failed. Status code: %s", r.status_code)
        return None
    # `raw.read` (decode_content=True) hands us the socket buffer directly, skipping
    # iter_content's per-8 KB Python loop; shutil pumps it in `_DOWNLOAD_CHUNK` blocks.
    r.raw.decode_content = True
    with open(output_path, "wb") as f:
        shutil.copyfileobj(r.raw, f, length=_DOWNLOAD_CHUNK)
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


async def process_track(
    session,
    track: TrackMetadata,
    representation,
    output_dir: Path,
    build_folder_structure: bool = True,
    lyrics_resp=None,
    on_step=None,
    wvd_path: str = "device.wvd",
    resolve_hi_res_cover: bool = False,
):
    """Download + decrypt + remux + tag, given an already-selected representation.

    `lyrics_resp` may be pre-fetched (single-track fast path); when None it is
    fetched here in parallel with the download + license. The cover JPEG is always
    fetched in that same parallel batch (so it's in hand by tagging time);
    `resolve_hi_res_cover` additionally runs the hi-res `textsearch` there — the
    single-track fast path defers that lookup out of `fetch_metadata` so it overlaps
    the download rather than blocking the license. `on_step(desc)` is an optional
    callback invoked at the start of each stage (drives the progress bar).

    Returns True when the track was skipped because an output file already exists
    (so callers can tally a "N file(s) already exist; skipped." note); otherwise None.
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
        return True

    # Per-track temp dir so concurrent downloads don't clobber each other's
    # encrypted/decrypted/remuxed scratch files.
    base_temp = output_dir / _TEMP_SUBDIR
    base_temp.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="dl-", dir=base_temp))
    encrypted_file = temp_dir / "encrypted.mp4"

    def fetch_cover():
        # The fast path leaves only the muse fallback on the track; upgrade it to
        # the hi-res master here (concurrently with the audio download) instead of
        # blocking the license back in fetch_metadata. Other paths already carry the
        # shared per-album hi-res URL, so just download the bytes.
        url = resolve_track_cover(session, track) if resolve_hi_res_cover else track.cover_url
        return download_artwork(url, str(temp_dir))

    step("downloading track")
    coros = [
        asyncio.to_thread(Keys.getContentKeys, session, track.asin, rep["pssh"], wvd_path),
        asyncio.to_thread(download_full_file, rep["base_url"], encrypted_file),
        asyncio.to_thread(fetch_cover),
    ]
    fetch_lyrics = lyrics_resp is None
    if fetch_lyrics:
        coros.append(asyncio.to_thread(session.get_track_lyrics, track.asin))
    results = await asyncio.gather(*coros)
    content_key = results[0]
    artwork_path = results[2]
    if fetch_lyrics:
        lyrics_resp = results[3]

    # The decrypt/remux/tag stages are blocking (subprocess + requests + mutagen);
    # run them off the event loop so other concurrent tracks keep making progress.
    step("decrypting")
    decrypted_mp4 = temp_dir / "decrypted_temp.mp4"
    try:
        await asyncio.to_thread(decrypt_mp4, encrypted_file, content_key, decrypted_mp4)
    except Exception:
        _log.error("decryption failed for %s", track.title, exc_info=True)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return

    step(f"remuxing to {extension.lstrip('.')}")
    media_temp = temp_dir / ("decoded_temp" + extension)
    await asyncio.to_thread(remux_copy, decrypted_mp4, media_temp)

    step("tagging metadata")
    lyrics_obj = Lyrics.from_xray(lyrics_resp)
    await asyncio.to_thread(
        tag_track, str(media_temp), track, lyrics_obj, str(temp_dir), tag_mode, artwork_path
    )

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
    """Album/general path: fetch the manifest, select a stream, then process.

    Propagates `process_track`'s return: True if the track already existed and was
    skipped, else None.
    """
    if on_step:
        on_step("fetching manifest")
    representation = await asyncio.to_thread(find_representation, session, track.asin, quality)
    return await process_track(
        session, track, representation, output_dir, build_folder_structure,
        on_step=on_step, wvd_path=wvd_path,
    )
