# amzdl

![Downloading an album](./assets/image.png)

Resolve an Amazon Music **track, album, artist, or playlist** to lossless
**FLAC** (and native **Opus / MP4 / AC-4** for lossy and spatial tiers) for personal
archival — fully tagged, with embedded cover art and a sidecar `.lrc` of synced
lyrics. Ships as a **CLI**. Based upon the [OrpheusDL module.](https://github.com/bascurtiz/orpheusdl-amazonmusic)
> [!NOTE]
> For educational purposes only. You are responsible for complying with Amazon
> Music's terms and your local laws.

## Install it with [uv](https://docs.astral.sh/uv/):
```zsh
uv tool install git+https://github.com/allclients2/downloader.git
```

---

## Table of contents

- [Setup from source](#setup-from-source)
  - [Setup with `uv` (recommended)](#setup-with-uv-recommended)
  - [Development & linting](#development--linting)
- [First run & authentication](#first-run--authentication)
- [Usage](#usage)
  - [Downloading](#downloading)
  - [Searching](#searching)
  - [Managing accounts](#managing-accounts)
  - [Quality tiers](#quality-tiers)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)

---

## Setup from source

Clone the repo **with submodules** — the Amazon Music API client lives in a git
submodule at `src/amazonmusic/`:

```bash
git clone --recurse-submodules <repo-url> downloader
cd downloader

# Already cloned without --recurse-submodules? Fetch it now:
git submodule update --init --recursive
```

> [!NOTE]
> Installing the project (below) puts an **`amzdl`** command on your `PATH`, so it
> runs from anywhere. It uses a `config/` folder in the **current working directory**
> when one exists (the repo-root dev layout), otherwise a per-user dir
> (`$AMZDL_CONFIG_DIR`, else `$XDG_CONFIG_HOME/amzdl`, else `~/.config/amzdl`).
> Downloads default to `~/Music/amzdl` (override with `--output`).

### Setup with `uv` (recommended)

[`uv`](https://docs.astral.sh/uv/) is a fast, all-in-one Python package and
environment manager. [Install it](https://docs.astral.sh/uv/getting-started/installation/)
first:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then install the project — `uv sync` creates `.venv`, installs the dependencies
(declared in `pyproject.toml`), and installs the `amzdl` console script:

```bash
uv sync

source .venv/bin/activate        # Windows: .venv\Scripts\activate

amzdl --version
```

To put `amzdl` on your `PATH` so it runs from anywhere, install it as a tool:

```bash
uv tool install --from . amzdl   # or: pipx install .
```

> [!NOTE]
> Prefer the standard tooling? A plain `python3 -m venv .venv` + `pip install .`
> (or `pip install -e .` for an editable checkout) works just as well — `uv` is only
> a faster convenience. Either way the dependencies come from `pyproject.toml`; there
> is no separate `requirements.txt` to install.

### Development & linting

Linting is handled by [**ruff**](https://docs.astral.sh/ruff/), configured under
`[tool.ruff]` in `pyproject.toml` (the `src/amazonmusic/` submodule is excluded). It
lives in the `dev` dependency group, installed alongside the project by `uv sync`:

```bash
uv run ruff check          # lint
uv run ruff check --fix    # lint and auto-fix
```

```bash
uv run pre-commit install            # install the git hook
uv run pre-commit run --all-files    # optional: run against the whole tree now
```

**Comment-policy**: Comments and docstrings should only be at the top of the file.
This is checked via a pre-commit hook.

After this, `git commit` lints (and auto-fixes) your changes automatically; a commit
is blocked if anything still fails.

Built and tested on **Windows 11 (25H2)** and **macOS Tahoe (26.4.1)** with
**Python 3.13**.

## First run & authentication

The first download (or your first `accounts --add`) launches an **interactive
browser sign-in**:

1. The tool prints an **OAuth URL** — open it in a browser.
2. Sign in to your Amazon account and pick your **2-letter region** (US, GB, DE, JP, …).
3. Copy the **post-login URL** from the address bar and paste it back into the prompt.

Behind the scenes this registers a device and saves the resulting credentials to
`config/credentials.bin`. After that, sign-in is not needed again — access tokens
refresh automatically. Multiple accounts (including several in the same region) can
be stored side by side.

---

## Usage

### Downloading

```text
usage: amzdl [-h] [--version] [--account ACCOUNT] [--output OUTPUT] [-v] [--quality TIER] [--wvd-path WVD_PATH]
               [--metadata-concurrency N]
               INPUT
```

| Flag | Description |
|---|---|
| `INPUT` | What to download (required): a bare **ASIN**, an **Amazon Music link** (any region; a `trackAsin=` selects one track), or a path to a **text file** of ASINs/links. Resolves a track, album, artist (whole discography), or playlist (catalog or user library) |
| `--output OUTPUT` | Directory to save files into (default: config `default_output`) |
| `--quality TIER` | Quality tier — linear ceiling (`LD`/`SD`/`HD`/`UHD` or a sub-tier) or a spatial tier; see [Quality tiers](#quality-tiers) (default: config `default_quality`) |
| `--account ACCOUNT` | Which stored account to use — customer id, name, or country code |
| `--wvd-path WVD_PATH` | Path to a Widevine device file to use instead of the built-in one |
| `--metadata-concurrency N` | How many album-metadata lookups to run at once when expanding an artist/playlist/batch (default: config `default_metadata_concurrency`) |
| `-v`, `--verbose` | Verbose logging + plain (non-animated) progress output |
| `--version` | Print the version and exit |

**Examples:**

```bash
# Download a track or album to ./downloads at HD (CD-quality FLAC)
amzdl B07JZ7PW6F --output downloads --quality HD

# A link — any region domain works; a trackAsin= picks just that one track
amzdl 'https://music.amazon.com/albums/B07JZ7PW6F?trackAsin=B07JZ8XYZ1'

# An artist's whole discography, or a playlist (catalog or your library)
amzdl B07JARTIST0

# A text file of ASINs/links (one per line) — downloaded as a single batch
amzdl tracks.txt

# Hi-res download with a specific account, verbose
amzdl B07JZ7PW6F --quality UHD --account US -v

# Dolby Atmos (spatial) — falls back to the best FLAC if the track has no Atmos
amzdl B07JZ7PW6F --quality SPATIAL_ATMOS
```

Downloads are **idempotent** — existing output files are skipped, so re-running an
album only fetches what's missing. Albums, artists, playlists, and batches download
several tracks concurrently, with a live progress bar (artists/playlists/batches show
a two-phase bar: metadata first, then the tracks). One bad input in a batch (or one
bad album in a discography) is skipped, not fatal.

### Searching

Don't have an ASIN or link? Search the catalog and pick a result to download. Any
type the download pipeline handles is searchable — `track`, `album`, `artist` (whole
discography), or `playlist`. Omitted query / `--type` are prompted for.

```bash
amzdl search "some name" --type track
amzdl search --type album              # prompts for the query
amzdl search "some name" --type artist  # whole discography
amzdl search                            # prompts for both
amzdl search x --type track --search-limit 10
```

`--search-limit` caps how many hits are shown (default: config `default_search_limit`).
The download flags above (`--account` / `--output` / `--quality` / `--wvd-path` /
`--metadata-concurrency`) apply to the picked result.

### Managing accounts

**Interactive menu** — list stored accounts, remove one (with a red confirmation),
`A` to add, `Q` to quit:

```bash
amzdl accounts
```

**Direct commands** — add or remove without the menu (`account` and `accounts` are
aliases; either spelling works with the menu or these flags):

```bash
# Add an account for a given region (omit the code to be prompted)
amzdl accounts --add US

# Remove a stored account by customer id, name, or country code
amzdl accounts --delete US
```

Account selection precedence when downloading: `--account` → config
`default_account` → the sole stored account → an interactive picker.

### Quality tiers

`--quality` (and config `default_quality`) accepts the full set of tiers the
Amazon Music API exposes. There are two kinds.

**Linear ladder** — a **ceiling**: the best available stream **at or below** the tier
is selected. Every stream is copied into its native container (never transcoded), so
the file extension follows the source: lossless **FLAC** for HD/UHD, native **Opus**
(`.opus`) for the lossy LD/SD tiers. A bare tier means "the top of that tier"; the
`_LOW`/`_MEDIUM`/`_HIGH` (and `_44`/`_48`) suffixes pick a finer step.

| Tier | Sub-tiers | Output | Meaning |
|---|---|---|---|
| `LD` | `LD_LOW`, `LD_MEDIUM` | `.opus` | Lowest definition (lossy Opus) |
| `SD` | `SD_LOW`, `SD_MEDIUM`, `SD_HIGH` | `.opus` | Standard definition (lossy Opus) |
| `HD` | `HD_44` | `.flac` | CD-quality lossless FLAC (16-bit) |
| `UHD` | `UHD_48` | `.flac` | Hi-res lossless FLAC (24-bit) |

**Spatial audio** — selected on its own axis (not the linear ceiling). These use
codecs FLAC can't carry, so they're stream-copied **untouched** into their native
container; if the track has no such stream it falls back to the best FLAC.

| Tier | Sub-tiers | Output | Notes |
|---|---|---|---|
| `SPATIAL_ATMOS` | `_LOW`, `_MEDIUM`, `_HIGH` | `.mp4` (Dolby Atmos / DD+, E-AC-3) with MP4 tags; AC-4 variants → raw `.ac4` (no tags) | Dolby Atmos |
| `SPATIAL_RA360` | `_L0` … `_L3` | `.mp4` (MPEG-H) with MP4 tags | Sony 360 Reality Audio |

> Spatial streams live in Amazon's 3D manifest variant, which the downloader
> requests automatically when a `SPATIAL_*` tier is chosen (this drops UHD for that
> request). AC-4 is delivered as a raw elementary stream with no container, so those
> files can't carry tags or embedded cover art.

---

## Configuration

On first run a `config/` folder is generated — in the current working directory if a
`config/` already exists there (the repo-root dev layout), otherwise in a per-user dir
(`$AMZDL_CONFIG_DIR`, else `$XDG_CONFIG_HOME/amzdl`, else `~/.config/amzdl`) —
containing:

- **`config.json`** — defaults plus the account registry:
  - `default_quality`, `default_output` — the per-run defaults the matching flags override
  - `default_wvd_path` — an optional Widevine device file to use instead of the built-in one (`--wvd-path`)
  - `default_concurrency` — how many tracks download at once (default 5)
  - `default_metadata_concurrency` — how many album-metadata lookups run at once when expanding an artist/playlist/batch (default 10; `--metadata-concurrency`)
  - `default_search_limit` — how many `search` hits to show (default 8; `--search-limit`)
  - `use_link_hints` — whether a link's up-front hints are used: its shape short-circuiting the type lookup for a known track, and its domain region auto-picking the matching stored account (default `true`)
  - `accounts` — table of signed-in accounts keyed by Amazon `customer_id`
  - `default_account` — which `customer_id` to use when several are stored
- **`credentials.bin`** — pickled per-account credentials (kept in sync with the
  `accounts` table on every login/refresh)

CLI flags (`--output` / `--quality` / `--wvd-path` / `--metadata-concurrency` /
`--search-limit`) override the matching config defaults for a single run; with no
flag, the config value is used. Missing keys in an existing `config.json` are
backfilled automatically.

> [!NOTE]
> `config/` and the `output/` & `downloads/` trees are all gitignored.

---

## How it works

All network access goes through the multi-region **Amazon Music mobile API** (the
`src/amazonmusic` submodule, from OrpheusDL). A device is registered once via OAuth,
and **every request is RSA-signed** (`x-adp-token` / `x-adp-signature`). The input is
first resolved to a list of content ids (a link is parsed for its id, a text file is
read line by line), then each track runs through a sequence of signed calls:

| Stage | Endpoint | What it does |
|---|---|---|
| **Auth** | device-registration OAuth | Registers a device (`adp_token`, RSA key, access/refresh tokens); signs every later request |
| **Metadata** | `muse` (`MusicEnsembleService.lookup`) | ASIN → title, artist, album, **disc/track numbers**, ISRC, release date, label, genre, and more — the rich tags written to each file |
| **Expansion** | artist `get_page` · `get_catalog_playlist` / `get_user_playlist` | An artist ASIN → its whole discography; a playlist id → its member tracks (catalog or user library) |
| **Cover art** | `textsearch` (`artOriginal`) | Fetches the full-resolution master image (≈1500–3000 px) instead of the 600×600 render; one search per album |
| **Manifest** | `getDashManifestsV2` | Returns a DASH MPD (lossless FLAC); audio is downloaded from its `BaseURL` |
| **Decryption** | `getLicenseForPlaybackV2` | Drives a `pywidevine` challenge with the web `TRACK_PSSH`; the content key feeds the in-process CENC (AES-CTR) decryptor, then a pure-Python remux writes the native container (`.flac` / `.opus` / spatial `.mp4` / `.ac4`) |
| **Lyrics** | `getLyricsByTrackAsinBatch` | Time-synced lyrics → embedded `LYRICS` tag + sidecar `.lrc` |

The `amzdl` package (under `src/amzdl/`) is a thin orchestration layer over a single
signed session, grouped by concern: `cli/` (UI, prompts, progress), `metadata/`
(resolving links, metadata, discography, playlists, the DASH manifest, search),
`process/` (download, decrypt, tagging, keys, lyrics), and `auth/` (auth, config, the
API subclass).

The upstream API client is tracked as a **read-only git submodule** (a sibling
package at `src/amazonmusic/`); the project's only local patches live in a thin
subclass at `src/amzdl/api/amzn_api.py`. Pull upstream fixes with
`git submodule update --remote`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Failed to get license: 400 … DEVICE_NOT_ELIGIBLE` (`ForbiddenException`) | The built-in Widevine device is invalid or has been revoked — Amazon won't issue a license to it. Provision a fresh, eligible Widevine device file and point `--wvd-path` / `default_wvd_path` at it. |
| `ImportError` for `amazonmusic` | Submodule not fetched — run `git submodule update --init --recursive`. |
| `amzdl: command not found` | The project isn't installed in the active environment — run `uv sync` (and activate `.venv`), or `uv tool install --from . amzdl` to put it on your `PATH`. |

For a full traceback on unexpected errors, re-run with `-v`.

---

## Special thanks to

- **[orpheusdl-amazonmusic](https://github.com/bascurtiz/orpheusdl-amazonmusic)** published by
  **bascurtiz** (originally created by [yuinachan](https://github.com/yuinachan)) — the Amazon Music mobile API client this project is built on, vendored as
  the `src/amazonmusic/` git submodule (RSA request signing, device registration, the
  multi-region endpoints).
- **[gamdl](https://github.com/glomatico/gamdl)** — inspiration for the whole project, the
  design reference for restoring a protected sample entry (the `frma`/`sinf` strip) that let
  us drop the external `mp4decrypt` dependency, and the reference for the pure-Python MP4
  (de)muxer.
- **[unshackle-services](https://github.com/n0stal6ic/unshackle-services)** — helping me implement playready support
- **Amazon** — for using a **secure** DRM.
