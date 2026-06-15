"""Static CLI screens: account selection, login, region, and search pickers.

These compose the palette, tree markers, and one-screen-at-a-time bookkeeping from
`ui` (the single source of truth for the CLI's look) into the interactive wizard
screens. Each helper first rewinds over whatever the previous screen printed (via
`ui._erase_pending`) so the wizard reads as one header replacing the next. The
animated download bar in `progress.py` follows the same convention.
"""

from amzdl.cli import ui
from amzdl.cli.ui import paint, faint, header
from amzdl.cli.ui import RED, YELLOW, MARK_TEE, MARK_CLOSE
from amzdl.cli.terminal import read_long_line


# ── account screens ───────────────────────────────────────────────────────────
def _account_row(name: str, region: str, index: int | None = None,
                 marker: str | None = None) -> str:
    """One ``├ [n] name — region`` row (number yellow, dash faint, text white)."""
    mark = marker or MARK_TEE
    label = f"{faint('[')}{paint(str(index), YELLOW)}{faint(']')} " if index is not None else ""
    return f"{mark} {label}{name}{faint(' — ')}{region}"


def prompt_region(title: str = "Add account") -> str:
    """Render the region screen and read the 2-letter code:

        │ downloader vX | Add account
        ╰ Region code (e.g. US):
    """
    ui._erase_pending()
    ui._emit(header(title))
    return ui._read(f"{MARK_CLOSE} {faint('Region code (e.g. US): ')}").strip()


def prompt_account(options: list[tuple[str, str]]) -> int | None:
    """Render the account selector and return the chosen 0-based index, or None
    for "Add". `options` is a list of ``(name, region)`` pairs. Re-prompts on
    invalid input:

        │ downloader vX | Select account
        ├ [1] Whoever — Japan
        ├ [2] test@example.com — United States of America
        ╰ Select [1-2], or A to Add:
    """
    ui._erase_pending()
    ui._emit(header("Select account"))
    for i, (name, region) in enumerate(options, 1):
        ui._emit(_account_row(name, region, index=i))
    n = len(options)
    prompt = f"{MARK_CLOSE} {faint(f'Select [1-{n}], or A to Add: ')}"
    while True:
        raw = ui._read(prompt).strip()
        if raw.lower() == "a":
            return None
        if raw.isdigit() and 1 <= int(raw) <= n:
            return int(raw) - 1


def prompt_manage_account(options: list[tuple[str, str]]) -> int | str:
    """Render the account manager and return the chosen 0-based index (to remove),
    ``"add"`` to sign in to a new account, or ``"quit"`` to exit. `options` is a
    list of ``(name, region)`` pairs. Re-prompts on invalid input:

        │ downloader vX | Manage accounts
        ├ [1] Whoever — Japan
        ├ [2] test@example.com — United States of America
        ╰ Select [1-2] to remove, A to add, or Q to quit:
    """
    ui._erase_pending()
    ui._emit(header("Manage accounts"))
    for i, (name, region) in enumerate(options, 1):
        ui._emit(_account_row(name, region, index=i))
    n = len(options)
    prompt = f"{MARK_CLOSE} {faint(f'Select [1-{n}] to remove, A to add, or Q to quit: ')}"
    while True:
        raw = ui._read(prompt).strip().lower()
        if raw in ("q", ""):
            return "quit"
        if raw == "a":
            return "add"
        if raw.isdigit() and 1 <= int(raw) <= n:
            return int(raw) - 1


def confirm_delete(name: str, region: str) -> bool:
    """Render the removal confirmation (in red) and return whether to proceed:

        │ downloader vX | Remove account
        ╰ Permanently remove name — region? [y/N]:
    """
    ui._erase_pending()
    ui._emit(header("Remove account"))
    label = f"{name}{' — '}{region}"
    question = paint(f"Permanently remove {label}? [y/N]: ", RED)
    return ui._read(f"{MARK_CLOSE} {question}").strip().lower() in ("y", "yes")


def prompt_oauth_url(app_title: str, url: str) -> str:
    """Render the browser sign-in screen and read the pasted post-login URL:

        │ downloader vX | Sign-in: Amazon Music (US)
        ├ 1. Open this URL: <url>
        ├ 2. After signing in you'll land on a blank / 'page not found' page.
        ├ 3. Copy that page's FULL URL from the address bar and paste it below.
        ╰ Paste the post-login URL and press Enter: [1234 chars]

    The URL is left in normal white so it stays selectable/clickable; everything
    else is faint. Called once per URL, so the JP two-step flow (Prime Video then
    Music) renders as two separate, fully-flushed screens. The pasted URL is read
    without echo (see `terminal.read_long_line`) — a live ``[N chars]`` counter on the
    prompt line acknowledges the paste instead, so the screen never scrolls.
    """
    # Built outside the f-strings: the step text contains apostrophes, and
    # backslash escapes inside f-string expressions are a SyntaxError before 3.12.
    step1 = faint("1. Open this URL: ")
    step2 = faint("2. After signing in you'll land on a blank / 'page not found' page.")
    step3 = faint("3. Copy that page's FULL URL from the address bar and paste it below.")
    ui._erase_pending()
    ui._emit(header(f"Sign-in: {app_title}"))
    ui._emit(f"{MARK_TEE} {step1}{url}")
    ui._emit(f"{MARK_TEE} {step2}")
    ui._emit(f"{MARK_TEE} {step3}")
    prompt = f"{MARK_CLOSE} {faint('Paste the post-login URL and press Enter: ')}"
    return ui._read(prompt, read_long_line, echo=False).strip()


def print_account_summary(title: str, options: list[tuple[str, str]]) -> None:
    ui._erase_pending()
    ui._emit(header(title))
    last = len(options) - 1
    for i, (name, region) in enumerate(options):
        ui._emit(_account_row(name, region, marker=MARK_CLOSE if i == last else MARK_TEE))


# ── search ─────────────────────────────────────────────────────────────────
def _search_title(type_label: str | None) -> str:
    """Header subtitle: ``Search tracks`` when the type is known, else ``Search``."""
    return f"Search {type_label}s" if type_label else "Search"


def prompt_search_query(type_label: str | None = None) -> str:
    """Render the query screen and read the search text:

        │ downloader vX | Search tracks
        ╰ Enter query:
    """
    ui._erase_pending()
    ui._emit(header(_search_title(type_label)))
    return ui._read(f"{MARK_CLOSE} {faint('Enter query: ')}").strip()


def prompt_search_type(types: tuple[str, ...]) -> str:
    """Render the type screen and read a valid search type. Re-prompts on
    unrecognised input. `types` is the tuple of selectable type labels:

        │ downloader vX | Search
        ╰ Search type (track, album, etc.):
    """
    ui._erase_pending()
    ui._emit(header("Search"))
    prompt = f"{MARK_CLOSE} {faint('Search type (track, album, etc.): ')}"
    while True:
        raw = ui._read(prompt).strip().lower()
        if raw.endswith("s") and raw[:-1] in types:
            raw = raw[:-1]
        if raw in types:
            return raw


def _search_row(fields: tuple[str, ...], index: int) -> str:
    """One ``├ [n] field - field - field`` row (number yellow, dashes faint)."""
    label = f"{faint('[')}{paint(str(index), YELLOW)}{faint(']')} "
    body = faint(" - ").join(f for f in fields if f)
    return f"{MARK_TEE} {label}{body}"


def prompt_search_results(
    type_label: str, rows: list[tuple[str, ...]]
) -> int | None:
    """Render the results picker and return the chosen 0-based index, or None to
    quit. `rows` is a list of display-column tuples. Re-prompts on invalid input:

        │ downloader vX | Search tracks
        ├ [1] Track Name - Album Name - Artist Name
        ├ [2] Track Name - Album Name - Artist Name
        ╰ Select [1-2] to download, or Q to quit:
    """
    ui._erase_pending()
    ui._emit(header(_search_title(type_label)))
    for i, fields in enumerate(rows, 1):
        ui._emit(_search_row(fields, i))
    n = len(rows)
    prompt = f"{MARK_CLOSE} {faint(f'Select [1-{n}] to download, or Q to quit: ')}"
    while True:
        raw = ui._read(prompt).strip().lower()
        if raw in ("q", ""):
            return None
        if raw.isdigit() and 1 <= int(raw) <= n:
            return int(raw) - 1
