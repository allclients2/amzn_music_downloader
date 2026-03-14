import subprocess
import asyncio
import os
import tempfile
import requests
from contextlib import contextmanager
from keys import Keys
from pathlib import Path
from metadata2 import Metadata2, AlbumMetadataV2, TrackMetadataV2
from metadata import TrackMetadata, AlbumMetadata
from lyrics import Lyrics
from util import safe_filename, build_output_filename
from mutagen.mp4 import MP4, MP4Cover
from mpd_info import TrackRepresentation
import shutil

@contextmanager
def download_temp_artwork(url: str, directory: str):
    if not url:
        yield None
        return

    tmp_path = None

    with tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".jpg",
        dir=directory
    ) as tmp:
        response = requests.get(
            url,
            timeout = 10,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            }
        )
        response.raise_for_status()
        tmp.write(response.content)
        tmp_path = tmp.name

    try:
        yield tmp_path
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

def download_full_file(rep, output_path=None):
    if output_path is None:
        output_path = f"{rep['id']}_full.bin"

    r = requests.get(rep["base_url"], stream=True)

    if r.status_code != 200:
        print(f"download failed. Status code: {r.status_code}")
        return None

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    return output_path

async def download_task(rep: list, encrypted_file: Path):
    print("downloading encrypted file...")
    return await asyncio.to_thread(
        download_full_file,
        rep,
        encrypted_file
    )

async def keys_task(rep, config, cookie_header):
    print("fetching content keys...")
    return await asyncio.to_thread(
        Keys.getContentKeys,
        rep["pssh"],
        config,
        cookie_header
    )

async def lyrics_task(track_metadatav2, config):
    print("fetching lyrics (if present)...")
    return await asyncio.to_thread(
        Metadata2.fetch_lyrics,
        track_metadatav2.asin,
        track_metadatav2.duration_seconds,
        config
    )

def embed_metadata_and_cover(
    mp4_path: str,
    track_metadatav1: TrackMetadata,
    track_metadatav2: TrackMetadataV2,
    album_metadatav1: AlbumMetadata,
    album_metadatav2: AlbumMetadataV2,
    artwork_path: str | None
):
    audio = MP4(mp4_path)
    
    # disc info
    disc_number = track_metadatav1.disc if hasattr(track_metadatav1, "disc") else 1

    # tracks on this disc only
    tracks_on_disc = [
        t for t in album_metadatav1.tracks
        if getattr(t, "disc", 1) == disc_number
    ]

    total_tracks_on_disc = len(tracks_on_disc)
    total_discs = max(
        (getattr(t, "disc", 1) for t in album_metadatav1.tracks),
        default=1
    )

    # track metadata
    audio["\xa9nam"] = track_metadatav2.title
    audio["\xa9ART"] = track_metadatav2.artist
    audio["trkn"] = [(track_metadatav1.track_number, total_tracks_on_disc)]
    audio["disk"] = [(track_metadatav1.disc, total_discs)]
    audio["\xa9day"] = album_metadatav2.release_date_iso or ""
    audio["\xa9cmt"] = "explicit" if track_metadatav2.is_explicit else ""

    # album metadata
    audio["\xa9alb"] = album_metadatav2.name
    audio["aART"] = album_metadatav2.artist
    audio["\xa9cpy"] = album_metadatav2.copyright or ""

    # lyrics
    if track_metadatav2.lyrics and track_metadatav2.lyrics.has_content():
        audio["\xa9lyr"] = track_metadatav2.lyrics.to_mp4_lyrics()

    # popularity
    if track_metadatav2.popularity is not None:
        audio["----:com.apple.iTunes:POPULARITY"] = [
            str(track_metadatav2.popularity).encode("utf-8")
        ]

    # artwork
    if artwork_path:
        with open(artwork_path, "rb") as img:
            cover = img.read()

        audio["covr"] = [
            MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)
        ]

    audio.save()

async def fetch_track(
    track_representation: TrackRepresentation,
    track_metadatav1: TrackMetadata,
    track_metadatav2: TrackMetadataV2,
    album_metadatav1: AlbumMetadata,
    album_metadatav2: AlbumMetadataV2,
    output_dir: Path,
    config,
    cookie_header: str,
    build_folder_structure: bool = True
):
    mpd_rep = track_representation.mpd_representation
    track_asin = track_representation.track_asin

    track_number = track_metadatav1.track_number
    track_name = track_metadatav1.track_name
    disc_number = track_metadatav1.disc

    if build_folder_structure:
        safe_album_artist_name = safe_filename(album_metadatav1.artist_name, False)
        safe_album_name = safe_filename(track_metadatav1.album_name, False)

        track_output_dir = output_dir / safe_album_artist_name / safe_album_name
    else:
        track_output_dir = output_dir

    output_filename = build_output_filename(disc_number, track_number, track_name)
    track_output_dir.mkdir(parents=True, exist_ok=True)
    output_file = track_output_dir / (output_filename + ".mp4")

    if os.path.exists(output_file):
        print(f"file {output_filename} already exists in output directory; skipping.")
        return
    
    # create a temporary directory we can work with
    temp_dir = (output_dir / ".downloader")
    temp_dir.mkdir(parents=True, exist_ok=True)

    print("downloading encrypted file...")
    encrypted_file = Path(temp_dir / "encrypted.mp4")
    
    download_coro = download_task(mpd_rep, encrypted_file)
    keys_coro = keys_task(mpd_rep, config, cookie_header)
    lyrics_coro = lyrics_task(track_metadatav2, config)

    content_key, _, json_lyrics = await asyncio.gather(
        keys_coro,
        download_coro,
        lyrics_coro
    )

    print("decrypting via mp4decrypt...")
    temp_output = Path(temp_dir / "decrypted_temp.mp4")

    cmd = [
        "mp4decrypt",
        "--key", content_key,
        str(encrypted_file),
        str(temp_output)
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("decryption failed?")
        return

    print("processing metadata...")
    lyrics_obj = Lyrics.from_json(json_lyrics)
    track_metadatav2.attach_lyrics(lyrics_obj)

    artwork_url = Metadata2.fetch_artwork_v2(track_asin, config)

    with download_temp_artwork(artwork_url, temp_dir) as artwork_path:
        embed_metadata_and_cover(
            mp4_path=temp_output,
            track_metadatav1=track_metadatav1,
            track_metadatav2=track_metadatav2,
            album_metadatav1=album_metadatav1,
            album_metadatav2=album_metadatav2,
            artwork_path=artwork_path
        )

    temp_output.rename(output_file)
    shutil.rmtree(temp_dir)

    if track_metadatav2.lyrics and track_metadatav2.lyrics.has_content():
        track_metadatav2.lyrics.save_lrc(track_output_dir / (output_filename + ".lrc"))

    print(f"finished, saved to: {output_file}")
