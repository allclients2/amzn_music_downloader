import discord
import os
import asyncio
import requests
import logging
import traceback
import multiprocessing
import re
import sys
import platform
from datetime import datetime, timezone
from pathlib import Path

from discord import app_commands
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN not set in .env")
print("DISCORD_TOKEN truncated:", DISCORD_TOKEN[:10])

TMPFILES_UPLOAD_URL = "https://tmpfiles.org/api/v1/upload"
OUTPUT_DIR = Path("./downloads")
OUTPUT_DIR.mkdir(exist_ok=True)

COOKIES_FILE = Path(os.getenv("COOKIES_FILE", "cookies.txt"))

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

BOT_START_TIME = datetime.now(timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────

def upload_file(filepath: Path) -> str:
    with open(filepath, "rb") as f:
        response = requests.post(
            TMPFILES_UPLOAD_URL,
            files={"file": (filepath.name, f)}
        )
    if not response.content:
        raise RuntimeError(f"tmpfiles.org returned empty response (status {response.status_code})")
    response.raise_for_status()
    data = response.json()
    if "data" not in data or "url" not in data.get("data", {}):
        raise RuntimeError(f"tmpfiles.org unexpected response: {data}")
    return data["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/")


def get_downloaded_files(directory: Path) -> list[Path]:
    return list(directory.rglob("*.mp4")) + list(directory.rglob("*.lrc"))


def sort_mp4s(mp4_files: list) -> list:
    """Sort by disc then track number, parsed from '{DISC} - {TRACK_NUM} {NAME}.mp4'."""
    def sort_key(p):
        m = re.match(r"^(\d+)\s*-\s*(\d+)", p.stem)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return sorted(mp4_files, key=sort_key)


def clear_directory(directory: Path):
    for f in directory.rglob("*"):
        if f.is_file():
            f.unlink()


def make_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Music Downloader Bot")
    return embed


def format_uptime(delta_seconds: float) -> str:
    """Format a duration in seconds to a human-readable string."""
    total = int(delta_seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def get_downloads_stats(directory: Path) -> tuple[int, int, str]:
    """Return (session_count, file_count, human_size) for the downloads folder."""
    sessions = [d for d in directory.iterdir() if d.is_dir()] if directory.exists() else []
    all_files = list(directory.rglob("*")) if directory.exists() else []
    all_files = [f for f in all_files if f.is_file()]
    total_bytes = sum(f.stat().st_size for f in all_files)

    if total_bytes < 1024:
        size_str = f"{total_bytes} B"
    elif total_bytes < 1024 ** 2:
        size_str = f"{total_bytes / 1024:.1f} KB"
    elif total_bytes < 1024 ** 3:
        size_str = f"{total_bytes / 1024 ** 2:.1f} MB"
    else:
        size_str = f"{total_bytes / 1024 ** 3:.2f} GB"

    return len(sessions), len(all_files), size_str


def check_download_config() -> tuple[list[str], bool]:
    """
    Check the health of download-related config without exposing sensitive values.
    Returns (lines, all_ok).
    """
    lines = []
    all_ok = True

    # Cookies file
    cookies_exists = COOKIES_FILE.exists()
    cookies_nonempty = cookies_exists and COOKIES_FILE.stat().st_size > 0
    if cookies_nonempty:
        lines.append(f"✅ `cookies.txt` — found ({COOKIES_FILE.stat().st_size / 1024:.1f} KB)")
    elif cookies_exists:
        lines.append(f"⚠️ `cookies.txt` — exists but is empty")
        all_ok = False
    else:
        lines.append(f"❌ `cookies.txt` — not found")
        all_ok = False

    # Output directory writable
    output_writable = os.access(OUTPUT_DIR, os.W_OK)
    if output_writable:
        lines.append(f"✅ Output directory — writable")
    else:
        lines.append(f"❌ Output directory — not writable")
        all_ok = False

    # Min bitrate env var (optional, just report what's set)
    min_bitrate = os.getenv("MIN_BITRATE")
    if min_bitrate:
        lines.append(f"✅ Min bitrate — `{min_bitrate}`")
    else:
        lines.append(f"ℹ️ Min bitrate — not set (uses max)")

    return lines, all_ok


# ── Process runner with progress updates ─────────────────────────────────────

async def run_in_process(
    task: str,
    asin: str,
    output_dir: str,
    interaction: discord.Interaction,
    base_embed_title: str,
) -> dict:
    import worker

    result_queue = multiprocessing.Queue()
    progress_queue = multiprocessing.Queue()

    proc = multiprocessing.Process(
        target=worker.run,
        args=(task, asin, output_dir, result_queue, progress_queue),
        daemon=True
    )
    proc.start()

    last_message = ""
    while proc.is_alive() or not result_queue.empty():
        if not result_queue.empty():
            break
        # Drain all pending progress messages, show the latest one
        latest = None
        while not progress_queue.empty():
            latest = progress_queue.get_nowait()
        if latest and latest != last_message:
            last_message = latest
            try:
                await interaction.edit_original_response(embed=make_embed(
                    base_embed_title, latest, discord.Color.orange()
                ))
            except Exception:
                pass
        await asyncio.sleep(0.5)

    proc.join(timeout=5)
    if result_queue.empty():
        raise RuntimeError("Worker process returned no result (may have crashed)")
    return result_queue.get()


# ── Startup / shutdown ────────────────────────────────────────────────────────

@client.event
async def on_ready():
    try:
        synced = await tree.sync()
        print(f"✅ Logged in as {client.user} — synced {len(synced)} command(s): {[c.name for c in synced]}")
        await client.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(type=discord.ActivityType.listening, name="/download")
        )
    except Exception as e:
        print(f"❌ Failed on ready: {e}")


@client.event
async def on_disconnect():
    try:
        await client.change_presence(status=discord.Status.offline)
    except Exception:
        pass


# ── Error helper ──────────────────────────────────────────────────────────────

async def send_error(interaction: discord.Interaction, e: Exception):
    full_tb = traceback.format_exc()
    print(f"❌ Error in command:\n{full_tb}")
    short = str(e)[:1800]
    await interaction.edit_original_response(embed=make_embed(
        "❌ Error", f"Something went wrong:\n```{short}```", discord.Color.red()
    ))


# ── Slash Commands ────────────────────────────────────────────────────────────

@discord.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@tree.command(name="download", description="Download a track or album by ASIN — type is auto-detected")
@app_commands.describe(asin="Track or album ASIN (e.g. B0CXYZ1234)")
async def download(interaction: discord.Interaction, asin: str):
    await interaction.response.defer(thinking=True)
    try:
        await interaction.edit_original_response(embed=make_embed(
            "⏳ Starting…", f"Looking up `{asin}`…", discord.Color.orange()
        ))

        # Use asin as subfolder so concurrent/repeated downloads don't collide
        output_dir = OUTPUT_DIR / asin
        output_dir.mkdir(parents=True, exist_ok=True)
        clear_directory(output_dir)

        result = await run_in_process(
            task="auto",
            asin=asin,
            output_dir=str(output_dir),
            interaction=interaction,
            base_embed_title="⏳ Downloading…",
        )

        if not result["ok"]:
            raise RuntimeError(result["error"])

        data = result["data"]
        cover_art_url = data.get("cover_art_url")

        await interaction.edit_original_response(embed=make_embed(
            "⏳ Uploading…", "Uploading files to tmpfiles.org…", discord.Color.orange()
        ))

        # ── Track result ──────────────────────────────────────────────────────
        if data["type"] == "track":
            files = get_downloaded_files(output_dir)
            if not files:
                raise FileNotFoundError("No output file was produced.")

            links = []
            for fp in sorted(files):
                url = await asyncio.to_thread(upload_file, fp)
                ext = fp.suffix.upper().lstrip(".")
                links.append(f"[{ext}]({url})")

            embed = discord.Embed(title="✅ Track Ready", color=discord.Color.green())
            embed.add_field(name="Track", value=data["track_name"], inline=True)
            embed.add_field(name="Artist", value=data["artist_name"], inline=True)
            embed.add_field(name="Album", value=data["album_name"], inline=False)
            embed.add_field(name="Download", value="  ·  ".join(links), inline=False)
            if cover_art_url:
                embed.set_thumbnail(url=cover_art_url)
            embed.set_footer(text="Links expire after 60 minutes (tmpfiles.org)")
            await interaction.edit_original_response(embed=embed)

        # ── Album result ──────────────────────────────────────────────────────
        elif data["type"] == "album":
            files = get_downloaded_files(output_dir)
            mp4_files = sort_mp4s([f for f in files if f.suffix == ".mp4"])
            lrc_files = {f.stem: f for f in files if f.suffix == ".lrc"}

            if not mp4_files:
                raise FileNotFoundError("No output files were produced.")

            lines = []
            for i, fp in enumerate(mp4_files, start=1):
                url = await asyncio.to_thread(upload_file, fp)
                line = f"`{i:02d}.` [{fp.stem}]({url})"
                lrc = lrc_files.get(fp.stem)
                if lrc:
                    lrc_url = await asyncio.to_thread(upload_file, lrc)
                    line += f" · [LRC]({lrc_url})"
                lines.append(line)

            chunks = [lines[i:i + 10] for i in range(0, len(lines), 10)]

            embed = discord.Embed(title="✅ Album Ready", color=discord.Color.green())
            embed.add_field(name="Album", value=data["album_name"], inline=True)
            embed.add_field(name="Artist", value=data["artist_name"], inline=True)
            embed.add_field(name="Tracks", value=str(data["track_count"]), inline=True)
            for idx, chunk in enumerate(chunks):
                embed.add_field(
                    name="Downloads" if idx == 0 else "\u200b",
                    value="\n".join(chunk),
                    inline=False
                )
            if cover_art_url:
                embed.set_thumbnail(url=cover_art_url)
            embed.set_footer(text="Links expire after 60 minutes (tmpfiles.org)")
            await interaction.edit_original_response(embed=embed)

    except Exception as e:
        await send_error(interaction, e)

@discord.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@tree.command(name="metadata", description="Fetch metadata for a track or album")
@app_commands.describe(asin="The track or album ASIN")
async def show_metadata(interaction: discord.Interaction, asin: str):
    await interaction.response.defer(thinking=True)
    try:
        output_dir = OUTPUT_DIR / asin
        output_dir.mkdir(parents=True, exist_ok=True)

        result = await run_in_process(
            task="metadata",
            asin=asin,
            output_dir=str(output_dir),
            interaction=interaction,
            base_embed_title="⏳ Fetching metadata…",
        )

        if not result["ok"]:
            raise RuntimeError(result["error"])

        data = result["data"]

        if data["type"] == "track":
            embed = discord.Embed(title="🎵 Track Metadata", color=discord.Color.blurple())
            embed.add_field(name="Track", value=data["track_name"], inline=True)
            embed.add_field(name="Artist", value=data["artist_name"], inline=True)
            embed.add_field(name="Album", value=data["album_name"], inline=True)
            embed.add_field(name="Track ASIN", value=f"`{data['track_asin']}`", inline=True)
            embed.add_field(name="Album ASIN", value=f"`{data['album_asin']}`", inline=True)
            if data.get("duration"):
                embed.add_field(name="Duration", value=data["duration"], inline=True)
            if data.get("is_explicit") is not None:
                embed.add_field(name="Explicit", value="Yes" if data["is_explicit"] else "No", inline=True)
            if data.get("lyrics_available") is not None:
                embed.add_field(name="Lyrics", value="Yes" if data["lyrics_available"] else "No", inline=True)
            if data.get("cover_art_url"):
                embed.set_thumbnail(url=data["cover_art_url"])
        else:
            embed = discord.Embed(title="💿 Album Metadata", color=discord.Color.blurple())
            embed.add_field(name="Album", value=data["album_name"], inline=True)
            embed.add_field(name="Artist", value=data["artist_name"], inline=True)
            embed.add_field(name="Album ASIN", value=f"`{data['album_asin']}`", inline=True)
            embed.add_field(name="Track Count", value=str(data["track_count"]), inline=True)
            if data.get("release_date"):
                embed.add_field(name="Released", value=data["release_date"], inline=True)
            if data.get("label"):
                embed.add_field(name="Label", value=data["label"], inline=True)
            if data.get("cover_art_url"):
                embed.set_thumbnail(url=data["cover_art_url"])
            from collections import defaultdict
            discs = defaultdict(list)
            if data.get("tracks_detailed"):
                for t in data["tracks_detailed"]:
                    discs[t["disc"]].append(t)
            else:
                for i, name in enumerate(data["tracks"]):
                    discs[1].append({"disc": 1, "track_number": i + 1, "name": name})

            lines = []
            shown = 0
            MAX_TRACKS = 50
            multi_disc = len(discs) > 1
            for disc_num in sorted(discs.keys()):
                if shown >= MAX_TRACKS:
                    break
                if multi_disc:
                    lines.append(f"**—  Disc {disc_num}  —**")
                for t in discs[disc_num]:
                    if shown >= MAX_TRACKS:
                        break
                    name = t["name"] if len(t["name"]) <= 42 else t["name"][:40] + "…"
                    lines.append(f"`{t['track_number']:02d} — {name}`")
                    shown += 1

            remaining = data["track_count"] - shown
            if remaining > 0:
                lines.append(f"*…and {remaining} more*")

            embed.add_field(name="Tracks", value="\n".join(lines) or "—", inline=False)

        await interaction.edit_original_response(embed=embed)

    except Exception as e:
        await send_error(interaction, e)


@discord.app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@tree.command(name="status", description="Show bot health, uptime, and configuration status")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        now = datetime.now(timezone.utc)
        uptime_seconds = (now - BOT_START_TIME).total_seconds()

        # ── Download config checks ────────────────────────────────────────────
        config_lines, all_config_ok = check_download_config()

        # ── Downloads folder stats ────────────────────────────────────────────
        session_count, file_count, cache_size = get_downloads_stats(OUTPUT_DIR)

        # ── Registered commands ───────────────────────────────────────────────
        registered_cmds = [cmd.name for cmd in tree.get_commands()]

        # ── Build embed ───────────────────────────────────────────────────────
        overall_color = discord.Color.green() if all_config_ok else discord.Color.orange()
        embed = discord.Embed(
            title="Bot Status",
            color=overall_color,
            timestamp=now,
        )

        # Bot info
        embed.add_field(
            name="Bot",
            value=(
                f"**Name:** {client.user}\n"
                f"**ID:** `{client.user.id}`\n"
                f"**Guilds:** {len(client.guilds)}\n"
                f"**Latency:** {round(client.latency * 1000)} ms"
            ),
            inline=True,
        )

        # Uptime
        embed.add_field(
            name="⏱️ Uptime",
            value=(
                f"**Up for:** {format_uptime(uptime_seconds)}\n"
                f"**Since:** <t:{int(BOT_START_TIME.timestamp())}:F>"
            ),
            inline=True,
        )

        # Commands
        embed.add_field(
            name="Commands",
            value="\n".join(f"`/{c}`" for c in sorted(registered_cmds)) or "None",
            inline=True,
        )

        # Download config
        embed.add_field(
            name=f"{'✅' if all_config_ok else '⚠️'} Download Config",
            value="\n".join(config_lines),
            inline=False,
        )

        # Downloads cache
        embed.add_field(
            name="💾 Cache",
            value=(
                f"**Sessions:** {session_count}\n"
                f"**Files:** {file_count}\n"
                f"**Size:** {cache_size}"
            ),
            inline=True,
        )

        # Runtime
        embed.add_field(
            name="Runtime",
            value=(
                f"**Python:** {sys.version.split()[0]}\n"
                f"**discord.py:** {discord.__version__}\n"
                f"**OS:** {platform.system()} {platform.release()}"
            ),
            inline=True,
        )

        embed.set_footer(text="Music Downloader Bot • Status checked at")
        await interaction.edit_original_response(embed=embed)

    except Exception as e:
        await send_error(interaction, e)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    client.run(DISCORD_TOKEN, log_handler=None)