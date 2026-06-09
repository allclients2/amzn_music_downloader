"""Console UI kit — the single source of truth for the CLI's look.

Every interactive surface (the download progress bar, the account selector, the
`login` prompts, the OAuth paste screen, error messages) shares one visual
language: a faint-grey "tree" of connectors (``│ ├ ╰``), a cyan ``downloader vX``
brand, a faint-grey-bold ``|`` separator, and a few semantic accents (green
``done`` in `progress.py`, red ``×`` for errors).

`progress.py` builds the animated download bar on top of the palette/markers
defined here; the static screens (account selection, login, errors) are rendered
by the `prompt_*` / `print_*` helpers below. Keeping the styling in one place is
what keeps the two consistent.

Only one screen is shown at a time: each `prompt_*`/`print_*` helper first rewinds
over and clears whatever the previous helper printed (tracked in `_pending_rows`),
the same `\033[nF\033[J` trick `progress.py` uses for its own block — so the wizard
reads as one header replacing the next rather than a growing stack. `progress.py`
calls `consume_pending_screen()` on its first paint so the download bar likewise
replaces the last setup screen. Only our own output is ever cleared; the shell
prompt above the first screen is left untouched.

Color is emitted only to a real terminal: `paint()` returns plain text when stdout
is not a TTY (piped output, the bot worker subprocess), so escape codes never leak
into logs.
"""

import re
import shutil
import sys
import termios
import unicodedata

from _version import VERSION

# ── ANSI palette ────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
FAINT = "\033[2m"
GREY = "\033[37m"
CYAN = "\033[34m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RED = "\033[31m"

# Color only when writing to a terminal; degrade to plain text otherwise.
_TTY = sys.stdout.isatty()


def paint(text: str, *codes: str) -> str:
    """Wrap `text` in the given ANSI codes (reset afterwards); plain when no TTY."""
    if not _TTY or not codes:
        return text
    return f"{''.join(codes)}{text}{RESET}"


def faint(text: str) -> str:
    """Faint grey — the default tone for connectors and unaccented screen text."""
    return paint(text, FAINT, GREY)


# ── brand + tree connectors ─────────────────────────────────────────────────
BRAND_TEXT = f"downloader v{VERSION}"   # raw (for width math); BRAND is the painted form

BRAND = paint(BRAND_TEXT, CYAN)
SEP = paint("|", FAINT, GREY, BOLD)     # header separator, rendered as f" {SEP} "

MARK_HEADER = faint("│")                # header line
MARK_TEE = paint("├", FAINT, GREY, BOLD)   # a body line with more below it
MARK_CLOSE = paint("╰", FAINT, GREY, BOLD)  # the closing/last line of a block


def header(*parts: str) -> str:
    """The header line: ``│ downloader vX | part | part…``.

    `parts` are subtitle segments (e.g. "Select account") appended in normal
    white, matching the title slot of `progress.py`'s header.
    """
    segments = [BRAND, *(p for p in parts if p)]
    return f"{MARK_HEADER} " + f" {SEP} ".join(segments)


# ── input primitives ────────────────────────────────────────────────────────
def read_long_line(prompt: str = "") -> str:
    """Read a single line that may be longer than the terminal's canonical line
    buffer (Amazon's post-login URLs are huge) by reading raw with ICANON off.

    ECHO is also disabled: echoing a pasted 1000-char URL verbatim would wrap it
    across a dozen rows, overflow the viewport, and make the terminal scroll — which
    breaks the one-screen-at-a-time redraw (the `\033[nF` rewind can't reach rows that
    scrolled above the visible top, leaving the previous screen's header behind).
    Instead, the prompt line is rewritten in place (via `\r`) with a live counter of
    how many characters have been received, so the user gets clear feedback that the
    paste registered while the prompt stays a single, non-scrolling line.

    Falls back to plain `input` when stdin is not a TTY.
    """
    if not sys.stdin.isatty():
        return input(prompt)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)

    def _show_count(n: int) -> None:
        # Redraw the prompt line in place with the running character count, so the
        # paste is visibly acknowledged without echoing the (huge) URL itself.
        # Normal white (not faint), matching how the pasted URL itself used to show.
        sys.stdout.write(f"\r\033[K{prompt}[{n} chars]")
        sys.stdout.flush()

    try:
        new = termios.tcgetattr(fd)
        # lflags: disable canonical (line-buffered) mode and input echo.
        new[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(fd, termios.TCSANOW, new)
        chars = []
        while True:
            c = sys.stdin.read(1)
            if c in ("\n", "\r"):
                break
            chars.append(c)
            _show_count(len(chars))
        return "".join(chars)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        sys.stdout.write("\n")
        sys.stdout.flush()


# ── one-screen-at-a-time bookkeeping ─────────────────────────────────────────
# Physical terminal rows of already-printed output that the *next* screen should
# overwrite, so only one screen is visible at a time. Notes and screen lines add
# to it as they print; the next screen (or the progress bar) rewinds over it.
_pending_rows = 0

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _term_cols() -> int:
    return shutil.get_terminal_size((80, 24)).columns


def _vis_rows(text: str, term_w: int) -> int:
    """Physical rows `text` occupies once wrapped at `term_w` (ANSI stripped,
    CJK/full-width counted as 2 columns), matching what the terminal will draw."""
    visible = _ANSI_RE.sub("", text)
    width = sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in visible)
    if term_w <= 0:
        return 1
    return max(1, -(-width // term_w))  # ceil(width / term_w)


def _erase_pending() -> None:
    """Rewind to the top of the previously printed screen/notes and clear it."""
    global _pending_rows
    if _TTY and _pending_rows:
        sys.stdout.write(f"\033[{_pending_rows}F\033[J")
        sys.stdout.flush()
    _pending_rows = 0


def _emit(line: str) -> None:
    """Print one screen line, tracking the physical rows it occupies."""
    global _pending_rows
    print(line)
    _pending_rows += _vis_rows(line, _term_cols())


def _read(prompt: str, reader=input, echo: bool = True) -> str:
    """Print `prompt`, read a line, and track the rows it occupied. With `echo`
    (the default, for `input`), the typed text stays on the prompt line until the
    trailing newline, so both are counted. With `echo=False` (the `read_long_line`
    paste, which suppresses its echo) only the prompt row is on screen, so only the
    prompt is counted — counting the unshown input would over-rewind the next erase."""
    global _pending_rows
    result = reader(prompt)
    _pending_rows += _vis_rows(prompt + result if echo else prompt, _term_cols())
    return result


def consume_pending_screen() -> None:
    """Clear the last screen before non-`ui` output (the progress bar) takes over."""
    _erase_pending()


# ── screens ──────────────────────────────────────────────────────────────────
def note(text: str) -> None:
    """Print one faint-grey informational line (the default for incidental output).

    Tracked like a screen line so the next screen / the progress bar overwrites it.
    """
    _emit(faint(text))


def print_error(message: str) -> None:
    """Render the error screen:

        │ downloader vX | Error
        ╰ × <message>
    """
    _erase_pending()
    _emit(header("Error"))
    _emit(f"{MARK_CLOSE} {paint('×', RED)} {message}")


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
    _erase_pending()
    _emit(header(title))
    return _read(f"{MARK_CLOSE} {faint('Region code (e.g. US): ')}").strip()


def prompt_account(options: list[tuple[str, str]]) -> int | None:
    """Render the account selector and return the chosen 0-based index, or None
    for "Add". `options` is a list of ``(name, region)`` pairs. Re-prompts on
    invalid input:

        │ downloader vX | Select account
        ├ [1] Whoever — Japan
        ├ [2] test@example.com — United States of America
        ╰ Select [1-2], or A to Add:
    """
    _erase_pending()
    _emit(header("Select account"))
    for i, (name, region) in enumerate(options, 1):
        _emit(_account_row(name, region, index=i))
    n = len(options)
    prompt = f"{MARK_CLOSE} {faint(f'Select [1-{n}], or A to Add: ')}"
    while True:
        raw = _read(prompt).strip()
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
    _erase_pending()
    _emit(header("Manage accounts"))
    for i, (name, region) in enumerate(options, 1):
        _emit(_account_row(name, region, index=i))
    n = len(options)
    prompt = f"{MARK_CLOSE} {faint(f'Select [1-{n}] to remove, A to add, or Q to quit: ')}"
    while True:
        raw = _read(prompt).strip().lower()
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
    _erase_pending()
    _emit(header("Remove account"))
    label = f"{name}{' — '}{region}"
    question = paint(f"Permanently remove {label}? [y/N]: ", RED)
    return _read(f"{MARK_CLOSE} {question}").strip().lower() in ("y", "yes")


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
    without echo (see `read_long_line`) — a live ``[N chars]`` counter on the prompt
    line acknowledges the paste instead, so the screen never scrolls.
    """
    # Built outside the f-strings: the step text contains apostrophes, and
    # backslash escapes inside f-string expressions are a SyntaxError before 3.12.
    step1 = faint("1. Open this URL: ")
    step2 = faint("2. After signing in you'll land on a blank / 'page not found' page.")
    step3 = faint("3. Copy that page's FULL URL from the address bar and paste it below.")
    _erase_pending()
    _emit(header(f"Sign-in: {app_title}"))
    _emit(f"{MARK_TEE} {step1}{url}")
    _emit(f"{MARK_TEE} {step2}")
    _emit(f"{MARK_TEE} {step3}")
    prompt = f"{MARK_CLOSE} {faint('Paste the post-login URL and press Enter: ')}"
    return _read(prompt, read_long_line, echo=False).strip()


def print_account_summary(title: str, options: list[tuple[str, str]]) -> None:
    _erase_pending()
    _emit(header(title))
    last = len(options) - 1
    for i, (name, region) in enumerate(options):
        _emit(_account_row(name, region, marker=MARK_CLOSE if i == last else MARK_TEE))
