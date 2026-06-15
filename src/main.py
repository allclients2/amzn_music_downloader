import argparse
import asyncio
import sys
from pathlib import Path

from auth import auth
from auth import config
from cli import prompts
from cli import ui
from process.download import download, download_batch
from metadata import links
from metadata.search import SEARCH_TYPES, normalize_type, search_catalog
from _version import VERSION


def _add_download_args(parser):
    """Add the settings flags shared by the download and search commands. A picked
    search result downloads with these, so both parsers must expose them identically
    (they all fall back to the matching `config.json` default when omitted)."""
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
        "--quality",
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
    parser.add_argument(
        "--metadata-concurrency",
        type=int,
        default=None,
        metavar="N",
        help="How many album-metadata lookups to run at once when downloading an "
             "artist (default: config default_metadata_concurrency)",
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=ui.paint(f"downloader v{VERSION}", ui.CYAN),
        epilog="Manage accounts: `python src/main.py accounts`",
    )

    parser.add_argument(
        "content_asin",
        metavar="INPUT",
        help="What to download: an ASIN, an Amazon Music link, or a path to a text "
             "file of ASINs/links (one per line). Resolves a track, album, artist, "
             "or playlist (catalog or user). A link's `trackAsin=` selects one track",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"downloader v{VERSION}",
    )
    _add_download_args(parser)

    return parser.parse_args(argv)


def parse_search_args(argv):
    parser = argparse.ArgumentParser(
        prog="main.py search",
        description=ui.paint(f"downloader v{VERSION} — search", ui.CYAN),
    )
    parser.add_argument(
        "query",
        nargs="?",
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
    _add_download_args(parser)
    return parser.parse_args(argv)


async def run_search(args):
    ui.setup_logging(args.verbose)

    settings = config.get_settings()
    quality = args.quality or settings["default_quality"]
    wvd_path = args.wvd_path or settings["default_wvd_path"]
    output_dir = Path(args.output or settings["default_output"])
    concurrency = settings["default_concurrency"]
    metadata_concurrency = args.metadata_concurrency or settings["default_metadata_concurrency"]
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
        query = prompts.prompt_search_query(search_type)
    if search_type is None:
        search_type = prompts.prompt_search_type(tuple(SEARCH_TYPES))

    results = await asyncio.to_thread(search_catalog, session, query, search_type, limit)
    if not results:
        ui.note(f"No {search_type}s found for '{query}'.")
        return

    choice = prompts.prompt_search_results(search_type, [r.fields for r in results])
    if choice is None:
        return

    # Fail fast if the picked result can't be decrypted (mirrors run_download).
    if not Path(wvd_path).exists():
        ui.print_error("Widevine device not found")
        sys.exit(1)

    # The picked result's type is known, so a track lets download() fetch the
    # manifest/lyrics concurrently with metadata instead of after it (unless the
    # `use_link_hints` config opt-out is off).
    type_hint = search_type if settings["use_link_hints"] else None
    await download(session, results[choice].asin, output_dir, quality, wvd_path,
                   plain=args.verbose, concurrency=concurrency,
                   metadata_concurrency=metadata_concurrency, type_hint=type_hint)


async def run_download(args):
    # Route logging through `ui` so the animated download bar can hold back stray
    # log lines that would otherwise corrupt its in-place redraw (see ui.setup_logging).
    ui.setup_logging(args.verbose)

    # CLI flags override the stored config defaults (generated on first run).
    settings = config.get_settings()
    quality = args.quality or settings["default_quality"]
    wvd_path = args.wvd_path or settings["default_wvd_path"]
    output_dir = Path(args.output or settings["default_output"])
    concurrency = settings["default_concurrency"]
    metadata_concurrency = args.metadata_concurrency or settings["default_metadata_concurrency"]

    # Resolve the argument to one or more content ids: a bare ASIN, an Amazon Music
    # link (ASIN extracted), or a text file of either (one per line).
    try:
        asins = links.resolve_inputs(args.content_asin)
    except (ValueError, OSError) as exc:
        ui.print_error(f"Could not parse input: {exc}")
        sys.exit(1)
    if not asins:
        ui.print_error("No ASINs or links found in input")
        sys.exit(1)

    # Fail fast with a friendly screen when the Widevine device file is missing:
    # decryption can't proceed without it, so don't prompt for an account or start
    # the download bar first.
    if not Path(wvd_path).exists():
        ui.print_error("Widevine device not found")
        sys.exit(1)

    # A link reveals up-front signals from its shape/domain (`LinkHint`): its content
    # type and its region. (A bare ASIN or text-file path yields an empty hint.) The
    # `use_link_hints` config opt-out disables both uses.
    hint = links.hint(args.content_asin) if settings["use_link_hints"] else links.LinkHint()

    # Builds a signed session from stored credentials; signs in interactively
    # (browser OAuth) on first use and persists the login. `--account` picks among
    # several stored accounts (id / name / country); omitted, a link's region picks
    # the matching stored account, else the default/sole one (or the picker).
    session = auth.get_session(account=args.account, country_hint=hint.country)

    # A text file of inputs gets the artist-style two-phase batch bar (resolve every
    # input's tracks, then download them all as one set). A bare ASIN/link keeps the
    # single-item path (errors surface as the Error screen via main()).
    source = links.input_file_label(args.content_asin)
    if source is not None:
        await download_batch(session, source, asins, output_dir, quality, wvd_path,
                             plain=args.verbose, concurrency=concurrency,
                             metadata_concurrency=metadata_concurrency)
    else:
        # A link encodes its type in its shape (e.g. /tracks/ or ?trackAsin=), so a
        # known-track input lets download() fetch the manifest/lyrics concurrently
        # with metadata; a bare ASIN (no hint) resolves the type from metadata first.
        # (`hint` is already empty when `use_link_hints` is off.)
        type_hint = hint.type
        await download(session, asins[0], output_dir, quality, wvd_path,
                       plain=args.verbose, concurrency=concurrency,
                       metadata_concurrency=metadata_concurrency,
                       type_hint=type_hint)


def _account_options():
    """The stored accounts as `(name, region)` pairs, parallel to their ids."""
    accounts = config.load_accounts()
    ids = list(accounts.keys())
    options = [auth._name_region(info) for info in accounts.values()]
    return ids, options


def _add_account(country=None):
    """Interactive browser sign-in that adds a new account to the store
    (config.json `accounts` + credentials.bin), then shows the updated list."""
    country = country or prompts.prompt_region()
    try:
        auth.login(country)
    except (ValueError, TypeError) as exc:
        # Bad/unknown country code — fail cleanly before the browser step.
        ui.print_error(f"Add account failed: {exc}")
        sys.exit(1)
    _, options = _account_options()
    prompts.print_account_summary("Account added", options)


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
            name, region = auth._name_region(info)
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

        choice = prompts.prompt_manage_account(options)
        if choice == "quit":
            return
        if choice == "add":
            _add_account()
            continue

        account_id = ids[choice]
        name, region = options[choice]
        if prompts.confirm_delete(name, region):
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


if __name__ == "__main__":
    main()
