import discord
import os
import asyncio
import requests
import logging
import traceback
import multiprocessing

from discord import app_commands
from pathlib import Path
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

# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


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
    url = data["data"]["url"]
    # Convert page URL to direct download URL
    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")


def get_downloaded_files(directory: Path) -> list[Path]:
    return list(directory.rglob("*.mp4")) + list(directory.rglob("*.lrc"))


def sort_mp4s(mp4_files: list) -> list:
    """Sort by disc number then track number, parsed from '{DISC} - {TRACK_NUM} {NAME}.mp4'."""
    import re
    def sort_key(p):
        m = re.match(r"^(\d+)\s*-\s*(\d+)", p.stem)
        if m:
            return (int(m.group(1)), int(m.group(2)))
        return (0, 0)
    return sorted(mp4_files, key=sort_key)


def clear_directory(directory: Path):
    for f in directory.rglob("*"):
        if f.is_file():
            f.unlink()


def make_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Music Downloader Bot")
    return embed


# ── Process runner ────────────────────────────────────────────────────────────

async def run_in_process(task: str, asin: str, output_dir: str = str(OUTPUT_DIR)) -> dict:
    import worker
    result_queue = multiprocessing.Queue()
    proc = multiprocessing.Process(
        target=worker.run,
        args=(task, asin, output_dir, result_queue),
        daemon=True
    )
    proc.start()
    while proc.is_alive() or not result_queue.empty():
        if not result_queue.empty():
            break
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
    # Called when the bot loses connection — presence goes offline automatically,
    # but we mark it explicitly so it shows offline on clean process exit too
    try:
        await client.change_presence(status=discord.Status.offline)
    except Exception:
        pass


# ── Error helper ──────────────────────────────────────────────────────────────

async def send_error(interaction: discord.Interaction, e: Exception, tb: str = None):
    """Send error embed and always print full traceback to console."""
    full_tb = tb or traceback.format_exc()
    print(f"❌ Error in command:\n{full_tb}")
    short = str(e)[:1800]  # Discord field limit
    await interaction.edit_original_response(embed=make_embed(
        "❌ Error", f"Something went wrong:\n```{short}```", discord.Color.red()
    ))


# ── Slash Commands ────────────────────────────────────────────────────────────

@tree.command(name="download", description="Download a track by its ASIN")
@app_commands.describe(asin="The track ASIN (e.g. B0CXYZ1234)")
async def download_track(interaction: discord.Interaction, asin: str):
    await interaction.response.defer(thinking=True)
    try:
        await interaction.edit_original_response(embed=make_embed(
            "⏳ Fetching metadata…", f"Retrieving info for `{asin}`…", discord.Color.orange()
        ))

        # Clear old downloads before starting
        clear_directory(OUTPUT_DIR)

        result = await run_in_process("download_track", asin)

        if not result["ok"]:
            if isinstance(result.get("data"), dict) and result["data"].get("error") == "not_a_track":
                await interaction.edit_original_response(embed=make_embed(
                    "❌ Not a track ASIN",
                    f"`{asin}` appears to be an album. Use `/album` instead.",
                    discord.Color.red()
                ))
                return
            raise RuntimeError(result["error"])

        data = result["data"]
        track_name = data["track_name"]
        artist_name = data["artist_name"]
        album_name = data["album_name"]

        await interaction.edit_original_response(embed=make_embed(
            "⏳ Uploading to tmpfiles.org…",
            f"Preparing download link for **{track_name}**…",
            discord.Color.orange()
        ))

        files = get_downloaded_files(OUTPUT_DIR)
        if not files:
            raise FileNotFoundError("No output file was produced.")

        links = []
        for fp in files:
            url = await asyncio.to_thread(upload_file, fp)
            ext = fp.suffix.upper().lstrip(".")
            links.append(f"[{ext}]({url})")

        embed = discord.Embed(title="✅ Track Ready", color=discord.Color.green())
        embed.add_field(name="Track", value=track_name, inline=True)
        embed.add_field(name="Artist", value=artist_name, inline=True)
        embed.add_field(name="Album", value=album_name, inline=False)
        embed.add_field(name="Download", value="  ·  ".join(links), inline=False)
        embed.set_footer(text="Links expire after 60 minutes (tmpfiles.org)")
        await interaction.edit_original_response(embed=embed)

    except Exception as e:
        await send_error(interaction, e)


@tree.command(name="album", description="Download a full album by its ASIN")
@app_commands.describe(asin="The album ASIN (e.g. B0CXYZ1234)")
async def download_album(interaction: discord.Interaction, asin: str):
    await interaction.response.defer(thinking=True)
    try:
        await interaction.edit_original_response(embed=make_embed(
            "⏳ Fetching album…", f"Looking up `{asin}`…", discord.Color.orange()
        ))

        album_dir = OUTPUT_DIR / asin
        album_dir.mkdir(parents=True, exist_ok=True)

        result = await run_in_process("download_album", asin, str(album_dir))

        if not result["ok"]:
            raise RuntimeError(result["error"])

        data = result["data"]
        album_name = data["album_name"]
        artist_name = data["artist_name"]
        track_count = data["track_count"]

        await interaction.edit_original_response(embed=make_embed(
            "⏳ Uploading to tmpfiles.org…",
            f"Uploading tracks for **{album_name}**…",
            discord.Color.orange()
        ))

        files = get_downloaded_files(album_dir)
        mp4_files = sort_mp4s([f for f in files if f.suffix == ".mp4"])
        if not mp4_files:
            raise FileNotFoundError("No output files were produced.")

        lrc_files = {f.stem: f for f in files if f.suffix == ".lrc"}

        lines = []
        for i, fp in enumerate(mp4_files, start=1):
            url = await asyncio.to_thread(upload_file, fp)
            line = f"`{i:02d}.` [{fp.stem}]({url})"
            if fp.stem in lrc_files:
                lrc_url = await asyncio.to_thread(upload_file, lrc_files[fp.stem])
                line += f" · [LRC]({lrc_url})"
            lines.append(line)

        chunks = [lines[i:i + 10] for i in range(0, len(lines), 10)]

        embed = discord.Embed(title="✅ Album Ready", color=discord.Color.green())
        embed.add_field(name="Album", value=album_name, inline=True)
        embed.add_field(name="Artist", value=artist_name, inline=True)
        embed.add_field(name="Tracks", value=str(track_count), inline=True)
        for idx, chunk in enumerate(chunks):
            embed.add_field(
                name="Downloads" if idx == 0 else "\u200b",
                value="\n".join(chunk),
                inline=False
            )
        embed.set_footer(text="Links expire after 60 minutes (tmpfiles.org)")
        await interaction.edit_original_response(embed=embed)

    except Exception as e:
        await send_error(interaction, e)


@tree.command(name="metadata", description="Fetch metadata for a track or album")
@app_commands.describe(asin="The track or album ASIN")
async def show_metadata(interaction: discord.Interaction, asin: str):
    await interaction.response.defer(thinking=True)
    try:
        result = await run_in_process("metadata", asin)

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
            track_list = "\n".join(f"`{i+1:02d}.` {name}" for i, name in enumerate(data["tracks"][:20]))
            if data["track_count"] > 20:
                track_list += f"\n…and {data['track_count'] - 20} more"
            embed.add_field(name="Tracks", value=track_list or "—", inline=False)

        await interaction.edit_original_response(embed=embed)

    except Exception as e:
        await send_error(interaction, e)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    multiprocessing.set_start_method("spawn", force=True)
    client.run(DISCORD_TOKEN, log_handler=None)