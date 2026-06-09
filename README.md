# Amazon Music Downloader
Archive music for personal use or whatever

Usage:
Make sure `mp4decrypt` is in the system path
Get a device widevine file and name it as `device.wvd`. Place it into the directory you are using the program from.

usage: main.py [-h] [--output-dir OUTPUT_DIR] [--cookies-file COOKIES_FILE] [-v] [--from-browser]
               [--browser {chrome,edge,firefox}] [--min-bitrate MIN_BITRATE]
               content_asin

Example:  `python src/main.py B07JZ7PW6F --from-browser --browser firefox --output-dir downloads`

Created/Tested on Windows 11 25H2 with Python 3.13.12. Not yet tested for other platforms.

This can also be used as a bot. Simply add your bot token to `.env` then run `src/bot.py`. Early feature; may have bugs.

---

## How it works (APIs & methods)

> **Update:** metadata and authentication now use the same APIs as OrpheusDL's
> `modules/amazonmusic` (vendored into `src/vendor/amazonmusic`). The old
> Playwright `/config.json` token scraping and the `music.amazon.co.jp/embed`
> HTML scraping are gone, and the tool is **multi-region** (no longer JP-only).
> Output is now **FLAC**. The `--cookies-file`, `--from-browser` and `--browser`
> flags were removed; current CLI:
>
> ```
> usage: main.py [-h] [--output-dir OUTPUT_DIR] [-v] [--min-bitrate MIN_BITRATE] content_asin
> ```
>
> Example: `python src/main.py B07JZ7PW6F --output-dir downloads`

**Requirements:** `mp4decrypt` and `ffmpeg` on PATH, a `device.wvd` Widevine
device file in the working directory, and the Python deps in `requirements.txt`.

### Authentication — device-registration OAuth + RSA request signing
First run opens an interactive browser sign-in: you're given an OAuth URL, sign
in, then paste the post-login URL back. The tool registers a device (obtaining an
`adp_token`, an RSA `device_private_key`, and access/refresh tokens) and pickles
the credentials to `credentials.bin`, keyed by region. Subsequent API calls are
**RSA-signed** (`x-adp-token` / `x-adp-signature`); the access token is refreshed
automatically. You pick your 2-letter region (US, GB, DE, JP, …) at login.

### Metadata — `muse`
`POST https://music.amazon.<tld>/<region>/api/muse/`
(`MusicEnsembleService.lookup`) — batched ASIN lookup returning `tracksList` /
`albumsList`: title, artist, album, **disc/track numbers**, track count, duration,
ISRC, explicit flag, popularity, song writers, release date, copyright, label,
genre, and 600×600 cover art. This replaces the old embed-page scraper (which was
the only source of disc/track numbers).

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
`<output-dir>/<album artist>/<album>/<disc> - <track> <title>.flac` (folder layout
unchanged), with a matching `.lrc` when synced lyrics are available.