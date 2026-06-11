import argparse
import asyncio
import sys
from pathlib import Path

import auth
import config
import ui
from fetch_track import fetch_track, process_track, purge_temp_dir
from metadata import fetch_metadata
from mpd_info import fetch_representations, select_representation
from search import SEARCH_TYPES, normalize_type, search_catalog
from _version import VERSION
from progress import Progress


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=ui.paint(f"downloader v{VERSION}", ui.CYAN),
        epilog="Manage accounts: `python src/main.py accounts`",
    )

    parser.add_argument(
        "content_asin",
        help="ASIN of the track or album to download",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"downloader v{VERSION}",
    )
    parser.add_argument(
        "--account",
        default=None,
        help="Which stored account to use: customer id, name, or country code "
             "(default: config default_account, else the only/selected account)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Directory to save the file (default: config default_output)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--default-quality",
        default=None,
        metavar="TIER",
        help="Quality tier: a linear ceiling (LD/SD/HD/UHD or a sub-tier like "
             "SD_HIGH, HD_44, UHD_48) or a spatial tier (SPATIAL_ATMOS[_LOW/_MEDIUM/"
             "_HIGH], SPATIAL_RA360[_L0..L3]). Default: config default_quality",
    )
    parser.add_argument(
        "--wvd-path",
        default=None,
        help="Path to the Widevine device file (default: config default_wvd_path)",
    )

    return parser.parse_args(argv)


def parse_search_args(argv):
    parser = argparse.ArgumentParser(
        prog="main.py search",
        description=ui.paint(f"downloader v{VERSION} — search", ui.CYAN),
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Search text (prompted for if omitted)",
    )
    parser.add_argument(
        "--type",
        default=None,
        choices=tuple(SEARCH_TYPES),
        help="What to search for (prompted for if omitted)",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=None,
        metavar="N",
        help="Max results to show (default: config default_search_limit)",
    )
    # Shared with the download path so a picked result downloads with these settings.
    parser.add_argument("--account", default=None, help="Stored account: id, name, or country")
    parser.add_argument("--output", default=None, help="Directory to save to (default: config)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose + plain progress")
    parser.add_argument("--default-quality", default=None, metavar="TIER", help="Quality tier (default: config)")
    parser.add_argument("--wvd-path", default=None, help="Widevine device file (default: config)")
    return parser.parse_args(argv)


async def run_search(args):
    ui.setup_logging(args.verbose)

    settings = config.get_settings()
    quality = args.default_quality or settings["default_quality"]
    wvd_path = args.wvd_path or settings["default_wvd_path"]
    output_dir = Path(args.output or settings["default_output"])
    concurrency = settings["default_concurrency"]
    limit = args.search_limit or settings["default_search_limit"]

    # `--account` selects a stored account (id / name / country); omitted, the
    # default_account is used, then the sole account, else the picker is shown.
    session = auth.get_session(account=args.account)

    # Resolve the search type and query, prompting for whichever was omitted. When
    # both are missing the query is asked first (its header stays the generic
    # "Search" until the type is known).
    search_type = normalize_type(args.type) if args.type else None
    query = args.query
    if query is None:
        query = ui.prompt_search_query(search_type)
    if search_type is None:
        search_type = ui.prompt_search_type(tuple(SEARCH_TYPES))

    results = await asyncio.to_thread(search_catalog, session, query, search_type, limit)
    if not results:
        ui.note(f"No {search_type}s found for '{query}'.")
        return

    choice = ui.prompt_search_results(search_type, [r.fields for r in results])
    if choice is None:
        return

    # Fail fast if the picked result can't be decrypted (mirrors run_download).
    if not Path(wvd_path).exists():
        ui.print_error("Widevine device not found")
        sys.exit(1)

    await download(session, results[choice].asin, output_dir, quality, wvd_path,
                   plain=args.verbose, concurrency=concurrency)


async def run_download(args):
    # Route logging through `ui` so the animated download bar can hold back stray
    # log lines that would otherwise corrupt its in-place redraw (see ui.setup_logging).
    ui.setup_logging(args.verbose)

    # CLI flags override the stored config defaults (generated on first run).
    settings = config.get_settings()
    quality = args.default_quality or settings["default_quality"]
    wvd_path = args.wvd_path or settings["default_wvd_path"]
    output_dir = Path(args.output or settings["default_output"])
    concurrency = settings["default_concurrency"]

    # Fail fast with a friendly screen when the Widevine device file is missing:
    # decryption can't proceed without it, so don't prompt for an account or start
    # the download bar first.
    if not Path(wvd_path).exists():
        ui.print_error("Widevine device not found")
        sys.exit(1)

    # Builds a signed session from stored credentials; signs in interactively
    # (browser OAuth) on first use and persists the login. `--account` picks among
    # several stored accounts (id / name / country); omitted uses the default/sole one.
    session = auth.get_session(account=args.account)

    await download(session, args.content_asin, output_dir, quality, wvd_path,
                   plain=args.verbose, concurrency=concurrency)


def _account_options():
    """The stored accounts as `(name, region)` pairs, parallel to their ids."""
    accounts = config.load_accounts()
    ids = list(accounts.keys())
    options = [
        (info.get("name") or "Unknown", info.get("region") or info.get("country") or "?")
        for info in accounts.values()
    ]
    return ids, options


def _add_account(country=None):
    """Interactive browser sign-in that adds a new account to the store
    (config.json `accounts` + credentials.bin), then shows the updated list."""
    country = country or ui.prompt_region()
    try:
        auth.login(country)
    except (ValueError, TypeError) as exc:
        # Bad/unknown country code — fail cleanly before the browser step.
        ui.print_error(f"Add account failed: {exc}")
        sys.exit(1)
    _, options = _account_options()
    ui.print_account_summary("Account added", options)


def run_accounts(argv):
    """`python src/main.py accounts` (alias: `account`) — account manager. With no
    flags it runs the interactive menu: lists stored accounts, selecting one prompts
    (in red) to remove it, `A` adds a new account, `Q` quits.

    With `--add [COUNTRY]` / `--delete ACCOUNT_ID` it skips the menu and runs the
    direct add/delete action."""
    if argv:
        parser = argparse.ArgumentParser(
            prog="main.py accounts",
            description="Add or remove an Amazon Music account.",
        )
        parser.add_argument(
            "--add",
            nargs="?",
            const="",
            default=None,
            metavar="COUNTRY",
            help="Sign in and add an account (optionally for a given 2-letter region).",
        )
        parser.add_argument(
            "--delete",
            default=None,
            metavar="ACCOUNT_ID",
            help="Remove a stored account by customer id, name, or country code.",
        )
        args = parser.parse_args(argv)

        if args.delete is not None:
            try:
                info = auth.delete_account(args.delete)
            except KeyError:
                ui.print_error(f"No stored account matching '{args.delete}'.")
                sys.exit(1)
            name = info.get("name") or "Unknown"
            region = info.get("region") or info.get("country") or "?"
            ui.note(f"Removed account '{name}' ({region}).")
        elif args.add is not None:
            _add_account(args.add or None)
        else:
            parser.error("specify --add or --delete")
        return

    while True:
        ids, options = _account_options()
        if not ids:
            ui.note("No accounts stored yet.")
            _add_account()
            continue

        choice = ui.prompt_manage_account(options)
        if choice == "quit":
            return
        if choice == "add":
            _add_account()
            continue

        account_id = ids[choice]
        name, region = options[choice]
        if ui.confirm_delete(name, region):
            auth.delete_account(account_id)
            ui.note(f"Removed account '{name}' ({region}).")
        # Loop back to the refreshed menu.


def main():
    argv = sys.argv[1:]
    verbose = "-v" in argv or "--verbose" in argv
    try:
        if argv and argv[0] in ("accounts", "account"):
            run_accounts(argv[1:])
        elif argv and argv[0] == "search":
            asyncio.run(run_search(parse_search_args(argv[1:])))
        else:
            asyncio.run(run_download(parse_args(argv)))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:
        # Render unexpected failures as the Error screen instead of a raw
        # traceback. SystemExit (argparse, the handled exits above) passes through;
        # verbose mode re-raises so the traceback is available for debugging.
        if verbose:
            raise
        ui.print_error(str(exc) or type(exc).__name__)
        sys.exit(1)


async def download(session, asin, output_dir, quality, wvd_path="device.wvd", plain=False,
                   concurrency=5):
    prog = Progress(asin=asin, plain=plain)
    prog.set_desc("fetching metadata, manifest & lyrics")

    try:
        # Single-track fast path: metadata, the DASH manifest, and lyrics all
        # depend only on the ASIN, so fetch them concurrently (we don't yet know
        # if the ASIN is a track or album). For an album ASIN the speculative
        # manifest/lyrics simply error and are ignored.
        meta_res, reps_res, lyrics_res = await asyncio.gather(
            asyncio.to_thread(fetch_metadata, session, asin),
            asyncio.to_thread(fetch_representations, session, asin, quality),
            asyncio.to_thread(session.get_track_lyrics, asin),
            return_exceptions=True,
        )

        if isinstance(meta_res, Exception):
            raise meta_res
        kind, meta = meta_res

        if kind == "track":
            prog.set_name(meta.title)
            representations = reps_res
            if isinstance(representations, Exception) or not representations:
                # Speculative manifest failed (rare for a track) — fetch directly.
                representations = fetch_representations(session, asin, quality)
            representation = select_representation(asin, representations, quality)
            lyrics_resp = None if isinstance(lyrics_res, Exception) else lyrics_res
            await process_track(
                session, meta, representation, output_dir, True, lyrics_resp,
                on_step=lambda desc: prog.update(desc), wvd_path=wvd_path,
            )
            prog.finish()
        else:
            # Album: download up to `concurrency` tracks at once. The aggregate
            # line counts completed tracks; each in-flight track gets its own
            # step-progress slot line.
            prog.set_name(meta.album_name)
            prog.begin_album(len(meta.tracks))
            sem = asyncio.Semaphore(concurrency)

            async def run_track(track):
                async with sem:
                    slot = prog.track_start(track.title)
                    try:
                        await fetch_track(
                            session, track, output_dir, quality,
                            on_step=lambda desc: prog.track_step(slot, desc),
                            wvd_path=wvd_path,
                        )
                    finally:
                        prog.track_done(slot)

            await asyncio.gather(*(run_track(t) for t in meta.tracks))
            prog.finish()
    except Exception:
        prog.abort()
        raise
    finally:
        purge_temp_dir(output_dir)


if __name__ == "__main__":
    main()
