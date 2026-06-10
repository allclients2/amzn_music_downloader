# Amazon Music Downloader
Archive music for personal use or whatever

Usage:
Make sure `mp4decrypt` is in the system path
Get a device widevine file and name it as `device.wvd`. Place it into the directory you are using the program from.

usage: main.py [-h] [--output OUTPUT] [-v] [--default-quality {SD,HD,UHD}]
               [--wvd-path WVD_PATH] content_asin

Example:  `python src/main.py B07JZ7PW6F --output downloads --default-quality HD`

Defaults (quality / output dir / wvd path) live in `config/config.json`,
generated on first run; the flags above override them.

Created/Tested on Windows 11 25H2 & macOS Tahoe 26.4.1 with Python 3.13.12.

This can also be used as a bot. Simply add your bot token to `.env` then run `src/bot.py`. Early feature; may have bugs.

---

## How it works (APIs & methods)

All network access goes through the multi-region Amazon Music mobile API
(the `src/amazonmusic` submodule): a device is registered once via OAuth,
and every request is RSA-signed. Resolving an ASIN to a tagged FLAC runs through
metadata, cover art, stream manifest, Widevine decryption, and lyrics, each a
signed call to a different endpoint.

**Requirements:** `mp4decrypt` and `ffmpeg` on PATH, a `device.wvd` Widevine
device file in the working directory, and the Python deps in `requirements.txt`.

### Authentication — device-registration OAuth + RSA request signing
First run opens an interactive browser sign-in: you're given an OAuth URL, sign
in, then paste the post-login URL back. The tool registers a device (obtaining an
`adp_token`, an RSA `device_private_key`, and access/refresh tokens) and pickles
the credentials to `config/credentials.bin`, keyed by region. Subsequent API calls are
**RSA-signed** (`x-adp-token` / `x-adp-signature`); the access token is refreshed
automatically. You pick your 2-letter region (US, GB, DE, JP, …) at login.

### Metadata — `muse`
`POST https://music.amazon.<tld>/<region>/api/muse/`
(`MusicEnsembleService.lookup`) — batched ASIN lookup returning `tracksList` /
`albumsList`: title, artist, album, **disc/track numbers**, track count, duration,
ISRC, explicit flag, popularity, song writers, release date, copyright, label,
and genre — the source of disc/track numbers and the rich tags written to each file.

### Cover art — `textsearch` (`artOriginal`)
The muse `image` field is only a 600×600 render. For full-resolution art the tool
queries the catalog search service
(`POST https://music.amazon.<tld>/<region>/api/textsearch/search/v1_1/`,
`TenzingTextSearchService.search`) for the album/track ASINs and uses the
`artOriginal.artUrl` master image (typically 1500–3000 px), exactly as OrpheusDL
does. One search per album (shared by every track); falls back to the 600×600
`image` if search returns nothing.

### Stream manifest — `getDashManifestsV2`
`POST https://music.amazon.<tld>/<region>/api/dmls/getDashManifestsV2` — returns a
DASH MPD (SIREN_KATANA, lossless FLAC). The encrypted audio is downloaded directly
from the manifest's `BaseURL`.

### Decryption — Widevine via `getLicenseForPlaybackV2`
The MPD's web `TRACK_PSSH` (the Widevine ContentProtection with no
`AmzMusic-2019` value) drives a `pywidevine` challenge against
`POST .../api/dmls/getLicenseForPlaybackV2` (`DrmType: WIDEVINE`). The returned
content key feeds `mp4decrypt`, then `ffmpeg -c copy` remuxes the lossless stream
into a `.flac` container, tagged via `mutagen`.

### Lyrics — `music-xray-service`
`POST https://music-xray-service.amazon.<tld>/`
(`MusicXrayService.getLyricsByTrackAsinBatch`) — time-synced lyric lines, written
both as an embedded `LYRICS` tag and a sidecar `.lrc`.

### Output
`<output-dir>/<album artist>/<album>/<disc> - <track> <title>.flac`, with a
matching `.lrc` when synced lyrics are available.