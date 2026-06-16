"""Tag a downloaded track and embed its cover art. `tag_track` dispatches on the container chosen by `fetch_track._output_spec` — Vorbis comments for FLAC/Opus, MP4 atoms for spatial `.mp4`, or skip for raw `.ac4`."""

import tempfile

import requests
from mutagen.flac import FLAC, Picture

from amzdl.metadata.metadata import TrackMetadata

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)
_FRONT_COVER = 3


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def download_artwork(url: str, directory: str):
    if not url:
        return None
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
        return tmp.name


def tag_track(media_path: str, track: TrackMetadata, lyrics, temp_dir: str,
              tag_mode: str = "flac", artwork_path=None):
    if tag_mode is None:
        return
    if tag_mode == "mp4":
        embed_metadata_and_cover_mp4(media_path, track, lyrics, artwork_path)
    elif tag_mode == "opus":
        embed_metadata_and_cover_opus(media_path, track, lyrics, artwork_path)
    else:
        embed_metadata_and_cover(media_path, track, lyrics, artwork_path)


def embed_metadata_and_cover_mp4(mp4_path: str, track: TrackMetadata, lyrics, artwork_path):
    from mutagen.mp4 import MP4, MP4Cover

    audio = MP4(mp4_path)
    audio.delete()

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
    audio.delete()
    _set_vorbis_fields(audio, track, lyrics)
    if artwork_path:
        audio.add_picture(_build_cover_picture(artwork_path))
    audio.save()


def embed_metadata_and_cover_opus(opus_path: str, track: TrackMetadata, lyrics, artwork_path):
    import base64

    from mutagen.oggopus import OggOpus

    audio = OggOpus(opus_path)
    audio.delete()
    _set_vorbis_fields(audio, track, lyrics)
    if artwork_path:
        pic = _build_cover_picture(artwork_path)
        audio["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
    audio.save()
