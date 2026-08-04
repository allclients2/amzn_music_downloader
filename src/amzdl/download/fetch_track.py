"""Download, decrypt, remux, and tag a single track. Each codec is stream-copied
into its natural container (HD/UHD FLAC, lossy Opus, or the spatial
`.mp4`/`.ac4` tiers) and filed at
`<output_dir>/<album_artist>/<album>/<disc> - <track> <title>.<ext>`."""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

import requests

from amzdl.download.keys import Keys
from amzdl.download.mpd_info import _AUDIO_EXTENSIONS, find_representation
from amzdl.metadata.lyrics import Lyrics
from amzdl.metadata.metadata import (
    TrackMetadata,
    resolve_artist_names,
    resolve_track_cover,
)
from amzdl.metadata.tagging import (
    artists_covering_display,
    artists_from_credits,
    download_artwork,
    tag_track,
)
from amzdl.remux.decrypt import decrypt_mp4
from amzdl.remux.remux import remux_ac4, remux_flac, remux_mp4, remux_opus
from amzdl.utils import (
    DEFAULT_FILE_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
    DEFAULT_MULTI_DISC_FILE_TEMPLATE,
    render_track_relpath,
)

_log = logging.getLogger("downloader.track")

_TEMP_SUBDIR = ".downloader"


async def _resolve_artists(
    session, track: TrackMetadata, credits: dict, use_asin_fallback: bool
) -> list[str]:
    artists = artists_from_credits(credits)
    if not artists and use_asin_fallback and track.contributor_asins:
        resolved = await asyncio.to_thread(
            resolve_artist_names, session, track.contributor_asins
        )
        artists = artists_covering_display(track.artist, resolved)
    if not artists and track.artist:
        artists = [track.artist]
    return artists

def _output_spec(codec):
    c = str(codec or "").lower()
    if c.startswith("opus"):
        return ".opus", "opus"
    if c.startswith(("ec-3", "ec3", "eac3", "ac-3", "ac3", "mha", "mhm")):
        return ".mp4", "mp4"
    if c.startswith(("ac-4", "ac4")):
        return ".ac4", None
    return ".flac", "flac"


def _track_url(session, track: TrackMetadata) -> str | None:
    if not track.album_asin:
        return None
    region = session.credentials.account_region
    return (
        f"https://music.amazon.{region.domain_tld}/albums/{track.album_asin}"
        f"?trackAsin={track.asin}&musicTerritory={region.country}"
    )


def _fetch_credits(session, asin: str) -> dict:
    try:
        return session.get_track_xray(
            asin, region_to_use=session.credentials.account_region, parse_credits=True
        ) or {}
    except Exception:
        _log.warning(
            "credits lookup failed for %s; tagging without credits", asin, exc_info=True
        )
        return {}


def _existing_download(search_dirs, output_filename: str):
    for directory in search_dirs:
        for ext in _AUDIO_EXTENSIONS:
            candidate = directory / (output_filename + ext)
            if candidate.exists():
                return candidate
    return None


def purge_temp_dir(output_dir: Path) -> None:
    shutil.rmtree(Path(output_dir) / _TEMP_SUBDIR, ignore_errors=True)


_DOWNLOAD_CHUNK = 1024 * 1024


def download_full_file(base_url: str, output_path, on_bytes=None):
    r = requests.get(base_url, stream=True)
    if r.status_code != 200:
        _log.error("download failed. Status code: %s", r.status_code)
        return None
    r.raw.decode_content = True
    total = int(r.headers.get("Content-Length") or 0)
    transferred = 0
    with open(output_path, "wb") as f:
        while True:
            chunk = r.raw.read(_DOWNLOAD_CHUNK)
            if not chunk:
                break
            f.write(chunk)
            transferred += len(chunk)
            if on_bytes:
                on_bytes(transferred, total)
    return output_path


_REMUXERS = {
    ".flac": remux_flac,
    ".opus": remux_opus,
    ".mp4": remux_mp4,
    ".ac4": remux_ac4,
}


def remux_copy(src_mp4: Path, dst: Path) -> None:
    remuxer = _REMUXERS.get(dst.suffix)
    if remuxer is None:
        raise RuntimeError(f"no remuxer for {dst.suffix!r} output")
    remuxer(src_mp4, dst)


async def process_track(
    session,
    track: TrackMetadata,
    representation,
    output_dir: Path,
    cfg,
    build_folder_structure: bool = True,
    lyrics_resp=None,
    on_step=None,
    resolve_hi_res_cover: bool = False,
    on_bytes=None,
):
    def step(desc):
        _log.debug(desc)
        if on_step:
            on_step(desc)

    if representation is None:
        _log.warning("no playable representation for %s; skipping.", track.title)
        return

    naming = cfg.naming
    rep = representation.mpd_representation
    extension, tag_mode = _output_spec(rep.get("codec"))

    folder_template = naming.folder_template if naming else DEFAULT_FOLDER_TEMPLATE
    if (track.total_discs or 0) > 1:
        file_template = (
            naming.multi_disc_file_template if naming
            else DEFAULT_MULTI_DISC_FILE_TEMPLATE
        )
    else:
        file_template = naming.file_template if naming else DEFAULT_FILE_TEMPLATE

    relpath = render_track_relpath(
        track, folder_template if build_folder_structure else "", file_template
    )
    rel_dir = relpath.parent
    output_filename = relpath.name
    track_output_dir = output_dir / rel_dir
    output_file = track_output_dir / (output_filename + extension)

    search_dirs = [track_output_dir]
    search_dirs.extend(Path(lib) / rel_dir for lib in (cfg.library_dirs or ()))
    existing = _existing_download(search_dirs, output_filename)
    if existing is not None:
        _log.info("file already exists (%s); skipping.", existing)
        return True

    base_temp = output_dir / _TEMP_SUBDIR
    base_temp.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="dl-", dir=base_temp))
    encrypted_file = temp_dir / "encrypted.mp4"

    def fetch_cover():
        url = (
            resolve_track_cover(session, track)
            if resolve_hi_res_cover
            else track.cover_url
        )
        return download_artwork(url, str(temp_dir))

    await asyncio.to_thread(session.ensure_fresh_token)

    step("downloading track")
    coros = [
        asyncio.to_thread(
            Keys.getContentKeys, session, track.asin, rep, cfg.device
        ),
        asyncio.to_thread(
            download_full_file, rep["base_url"], encrypted_file, on_bytes
        ),
        asyncio.to_thread(fetch_cover),
        asyncio.to_thread(_fetch_credits, session, track.asin),
    ]
    fetch_lyrics = lyrics_resp is None
    if fetch_lyrics:
        coros.append(asyncio.to_thread(session.get_track_lyrics, track.asin))
    results = await asyncio.gather(*coros)
    content_key = results[0]
    artwork_path = results[2]
    credits = results[3]
    if fetch_lyrics:
        lyrics_resp = results[4]

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
    artists = await _resolve_artists(
        session, track, credits, cfg.resolve_artists_from_asins
    )
    await asyncio.to_thread(
        tag_track, str(media_temp), track, lyrics_obj, str(temp_dir), tag_mode,
        artwork_path,
        _track_url(session, track), credits, rep.get("reference_loudness"), artists,
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
    cfg,
    build_folder_structure: bool = True,
    on_step=None,
):
    if on_step:
        on_step("fetching manifest")
    representation = await asyncio.to_thread(
        find_representation, session, track.asin, cfg.quality
    )
    return await process_track(
        session, track, representation, output_dir, cfg, build_folder_structure,
        on_step=on_step,
    )
