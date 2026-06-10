# amzn_music_downloader

Resolve an Amazon Music **ASIN** to lossless **FLAC, OPUS, MP4, AC4** files for personal archival —
fully tagged, with embedded cover art and a sidecar `.lrc` of synced lyrics. Ships
as a **CLI**.

Every file lands at:

```
<output-dir>/<album artist>/<album>/<disc> - <track> <title>.flac
```

> [!NOTE]
> For personal archival use only. You are responsible for complying with Amazon
> Music's terms and your local laws.

Built and tested on **Windows 11 (25H2)** and **macOS Tahoe (26.4.1)** with
**Python 3.13**.

---

## Table of contents

- [Requirements](#requirements)
- [Setup](#setup)
  - [Setup with `uv` (recommended)](#setup-with-uv-recommended)
  - [Setup with `pip` + venv](#setup-with-pip--venv)
- [First run & authentication](#first-run--authentication)
- [Usage](#usage)
  - [Downloading](#downloading)
  - [Managing accounts](#managing-accounts)
  - [Quality tiers](#quality-tiers)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Troubleshooting](#troubleshooting)

---

## Requirements

You need three things that **cannot** be installed via `pip`:

| Requirement | What it is | How to get it |
|---|---|---|
| **`ffmpeg`** | Remuxes the decrypted stream into a `.flac` container | [ffmpeg.org](https://ffmpeg.org/download.html) — must be on `PATH` |
| **`mp4decrypt`** | Decrypts the downloaded audio (part of Bento4) | [Bento4 binaries](https://www.bento4.com/downloads/) — must be on `PATH` |
| **`device.wvd`** | A provisioned **Widevine** device file used for license/decryption | Provide your own; place it in the working directory (gitignored, never in the repo) |

Plus **Python ≥ 3.11** (developed and tested on 3.13).

> [!IMPORTANT]
> Without a valid `device.wvd`, decryption fails and the tool exits early. Its
> location defaults to `device.wvd` in the working directory and can be overridden
> with `--wvd-path` or the `default_wvd_path` config key.

---

## Setup

Clone the repo **with submodules** — the Amazon Music API client lives in a git
submodule at `src/amazonmusic/`:

```bash
git clone --recurse-submodules <repo-url> downloader
cd downloader

# Already cloned without --recurse-submodules? Fetch it now:
git submodule update --init --recursive
```

> [!NOTE]
> Everything is run **from the repo root** (e.g. `python src/main.py …`), not via
> `python -m`. This is what makes the flat imports and the `amazonmusic` package
> resolve, and what anchors `device.wvd` / `config/` to the repo root.

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

Then create an environment and install dependencies:

```bash
# Create a virtual environment pinned to Python 3.13 (uv will fetch it if needed)
uv venv --python 3.13

# Install the dependencies into it
uv pip install -r requirements.txt
```

Run commands either by activating the venv or with `uv run`:

```bash
# Option A — activate, then run normally
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python src/main.py --version

# Option B — no activation needed
uv run python src/main.py --version
```

### Setup with `pip` + venv

Prefer the standard toolchain? A plain virtual environment works just as well:

```bash
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

---

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
python src/main.py [-h] [--version] [--account ACCOUNT] [--output OUTPUT]
                   [-v] [--default-quality TIER] [--wvd-path WVD_PATH]
                   content_asin
```

| Flag | Description |
|---|---|
| `content_asin` | ASIN of the **track or album** to download (required) |
| `--output OUTPUT` | Directory to save files into (default: config `default_output`) |
| `--default-quality TIER` | Quality tier — linear ceiling (`LD`/`SD`/`HD`/`UHD` or a sub-tier) or a spatial tier; see [Quality tiers](#quality-tiers) (default: config `default_quality`) |
| `--account ACCOUNT` | Which stored account to use — customer id, name, or country code |
| `--wvd-path WVD_PATH` | Path to the Widevine device file (default: config `default_wvd_path`) |
| `-v`, `--verbose` | Verbose logging + plain (non-animated) progress output |
| `--version` | Print the version and exit |

**Examples:**

```bash
# Download a track or album to ./downloads at HD (CD-quality FLAC)
python src/main.py B07JZ7PW6F --output downloads --default-quality HD

# Hi-res download with a specific account, verbose
python src/main.py B07JZ7PW6F --default-quality UHD --account US -v

# Dolby Atmos (spatial) — falls back to the best FLAC if the track has no Atmos
python src/main.py B07JZ7PW6F --default-quality SPATIAL_ATMOS
```

Downloads are **idempotent** — existing output files are skipped, so re-running an
album only fetches what's missing. Albums download several tracks concurrently.

### Managing accounts

**Interactive menu** — list stored accounts, remove one (with a red confirmation),
`A` to add, `Q` to quit:

```bash
python src/main.py accounts
```

**Direct commands** — add or remove without the menu (`account` and `accounts` are
aliases; either spelling works with the menu or these flags):

```bash
# Add an account for a given region (omit the code to be prompted)
python src/main.py accounts --add US

# Remove a stored account by customer id, name, or country code
python src/main.py accounts --delete US
```

Account selection precedence when downloading: `--account` → config
`default_account` → the sole stored account → an interactive picker.

### Quality tiers

`--default-quality` (and config `default_quality`) accepts the full set of tiers the
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

On first run a `config/` folder is generated at the repo root containing:

- **`config.json`** — defaults plus the account registry:
  - `default_quality`, `default_output`, `default_wvd_path`, `default_concurrency`
  - `accounts` — table of signed-in accounts keyed by Amazon `customer_id`
  - `default_account` — which `customer_id` to use when several are stored
- **`credentials.bin`** — pickled per-account credentials (kept in sync with the
  `accounts` table on every login/refresh)

CLI flags (`--output` / `--default-quality` / `--wvd-path`) override the matching
config defaults for a single run; with no flag, the config value is used. Missing
keys in an existing `config.json` are backfilled automatically.

> [!NOTE]
> `config/`, `device.wvd`, `.env`, and the `output/` & `downloads/` trees are all
> gitignored.

---

## How it works

All network access goes through the multi-region **Amazon Music mobile API** (the
`src/amazonmusic` submodule, from OrpheusDL). A device is registered once via OAuth,
and **every request is RSA-signed** (`x-adp-token` / `x-adp-signature`). Resolving an
ASIN to a tagged FLAC runs through a sequence of signed calls:

| Stage | Endpoint | What it does |
|---|---|---|
| **Auth** | device-registration OAuth | Registers a device (`adp_token`, RSA key, access/refresh tokens); signs every later request |
| **Metadata** | `muse` (`MusicEnsembleService.lookup`) | ASIN → title, artist, album, **disc/track numbers**, ISRC, release date, label, genre, and more — the rich tags written to each file |
| **Cover art** | `textsearch` (`artOriginal`) | Fetches the full-resolution master image (≈1500–3000 px) instead of the 600×600 render; one search per album |
| **Manifest** | `getDashManifestsV2` | Returns a DASH MPD (lossless FLAC); audio is downloaded from its `BaseURL` |
| **Decryption** | `getLicenseForPlaybackV2` | Drives a `pywidevine` challenge with the web `TRACK_PSSH`; the content key feeds `mp4decrypt`, then `ffmpeg -c copy` remuxes to `.flac` |
| **Lyrics** | `getLyricsByTrackAsinBatch` | Time-synced lyrics → embedded `LYRICS` tag + sidecar `.lrc` |

The upstream API client is tracked as a **read-only git submodule**; the project's
only local patches live in a thin subclass at `src/amzn_api.py`. Pull upstream fixes
with `git submodule update --remote`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Widevine device not found` | No `device.wvd` at the expected path — place one in the repo root or pass `--wvd-path`. |
| `ImportError` for `amazonmusic` | Submodule not fetched — run `git submodule update --init --recursive`. |
| `mp4decrypt` / `ffmpeg` not found | Not on `PATH` — install them and reopen your shell. |
| Imports fail when running the script | You ran it from somewhere other than the repo root — `cd` to the repo root and run `python src/main.py …`. |

For a full traceback on unexpected errors, re-run with `-v`.
