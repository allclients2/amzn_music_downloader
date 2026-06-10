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
is not a TTY (piped output), so escape codes never leak
into logs.
"""

import logging
import re
import shutil
import sys
import unicodedata

from _version import VERSION

# Raw single-character terminal input is platform-specific: `termios` is POSIX
# (macOS/Linux), `msvcrt` is its Windows counterpart. Only one is ever importable
# on a given OS; `read_long_line` dispatches on whichever is present.
try:
    import termios  # POSIX
except ImportError:  # pragma: no cover - Windows
    termios = None
try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


def _enable_windows_ansi() -> None:
    """Turn on ANSI/VT escape-sequence processing for the Windows console.

    Both these screens and `progress.py` are built entirely from ANSI codes
    (colour, plus the ``\\033[nF`` cursor rewind that powers the in-place
    redraw). Windows 10+ consoles understand them but need
    ENABLE_VIRTUAL_TERMINAL_PROCESSING set first — without it the raw escape
    sequences print as literal garbage. No-op on every non-Windows platform."""
    if sys.platform != "win32":  # pragma: no cover - exercised only on Windows
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    for handle_id in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
        handle = kernel32.GetStdHandle(handle_id)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | enable_vt)


_enable_windows_ansi()

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

    Falls back to plain `input` when stdin is not a TTY, and dispatches to the
    POSIX (`termios`) or Windows (`msvcrt`) raw-read backend by platform.
    """
    if not sys.stdin.isatty():
        return input(prompt)
    if termios is not None:
        return _read_long_line_posix(prompt)
    if msvcrt is not None:  # pragma: no cover - Windows
        return _read_long_line_windows(prompt)
    return input(prompt)  # pragma: no cover - no raw-read backend available


def _show_paste_count(prompt: str, n: int) -> None:
    """Redraw the prompt line in place with the running character count, so the
    paste is visibly acknowledged without echoing the (huge) URL itself."""
    sys.stdout.write(f"\r\033[K{prompt}[{n} chars]")
    sys.stdout.flush()


def _read_long_line_posix(prompt: str) -> str:
    """`read_long_line` on POSIX: drop ICANON/ECHO via termios, read raw to EOL."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
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
            _show_paste_count(prompt, len(chars))
        return "".join(chars)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)
        sys.stdout.write("\n")
        sys.stdout.flush()


def _read_long_line_windows(prompt: str) -> str:  # pragma: no cover - Windows
    """`read_long_line` on Windows: read wide chars via `msvcrt.getwch`, which is
    already unbuffered and non-echoing, so the same no-scroll paste counter works."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    chars = []
    while True:
        c = msvcrt.getwch()
        if c in ("\r", "\n"):
            break
        if c == "\x03":  # Ctrl-C: honour interrupt instead of swallowing it.
            raise KeyboardInterrupt
        if c in ("\x00", "\xe0"):  # arrow/function keys: 2-char sequence, skip both.
            msvcrt.getwch()
            continue
        if c == "\b":  # backspace
            if chars:
                chars.pop()
        else:
            chars.append(c)
        _show_paste_count(prompt, len(chars))
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(chars)


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


def adopt_pending_rows(rows: int) -> None:
    """Register `rows` physical rows drawn outside `ui` (the progress bar's block)
    as the pending screen, so the next `ui` screen erases them.

    The progress bar paints and rewinds its own block without touching
    `_pending_rows`; on a download error it hands the leftover block off here so
    `print_error`'s `_erase_pending` clears it (one screen at a time) instead of
    leaving the half-finished bar stranded above the error."""
    global _pending_rows
    _pending_rows = max(0, rows)


# ── log buffering while the progress bar owns the screen ─────────────────────
# The animated download bar redraws in place by rewinding a fixed number of rows
# (`progress.py`), which assumes nothing else writes to the terminal between
# frames. A stray log line on stderr (the root handler installed below) shifts the
# cursor and desyncs that rewind, leaving a duplicated header. So while the bar is
# live we *buffer* log records and release them once it finishes — the bar keeps a
# clean screen, and warnings still surface (just beneath the completed/aborted bar
# rather than corrupting it). Verbose runs use the plain renderer and never start
# buffering, so their logs stream live as before.
_log_handler = None


class _BarAwareHandler(logging.StreamHandler):
    """A stderr `StreamHandler` that buffers records while a progress bar owns the
    screen, flushing them in order once the bar releases it."""

    def __init__(self):
        super().__init__()  # default stream: sys.stderr
        self._buffering = False
        self._buffer = []

    def emit(self, record):
        # logging calls this under `self.lock`, so the buffer access is serialized
        # with begin_bar/end_bar and concurrent emits from worker threads.
        if self._buffering:
            self._buffer.append(record)
            return
        super().emit(record)

    def begin_bar(self):
        with self.lock:
            self._buffering = True

    def end_bar(self):
        with self.lock:
            self._buffering = False
            buffered, self._buffer = self._buffer, []
            for record in buffered:
                super().emit(record)


def setup_logging(verbose: bool) -> None:
    """Install the bar-aware handler on the root logger (replaces `basicConfig`).

    All records — the project's `downloader.*` loggers plus any third-party
    (pywidevine/urllib3/asyncio) loggers that propagate to root — flow through one
    handler that can be told to hold output while the download bar is animating."""
    global _log_handler
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.WARNING)
    if _log_handler is None:
        _log_handler = _BarAwareHandler()
        _log_handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        root.addHandler(_log_handler)


def begin_bar_logging() -> None:
    """Start buffering log output (called when an animated bar takes the screen)."""
    if _log_handler is not None:
        _log_handler.begin_bar()


def end_bar_logging() -> None:
    """Stop buffering and flush whatever accumulated while the bar was live."""
    if _log_handler is not None:
        _log_handler.end_bar()


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


# ── search ─────────────────────────────────────────────────────────────────
def _search_title(type_label: str | None) -> str:
    """Header subtitle: ``Search tracks`` when the type is known, else ``Search``."""
    return f"Search {type_label}s" if type_label else "Search"


def prompt_search_query(type_label: str | None = None) -> str:
    """Render the query screen and read the search text:

        │ downloader vX | Search tracks
        ╰ Enter query:
    """
    _erase_pending()
    _emit(header(_search_title(type_label)))
    return _read(f"{MARK_CLOSE} {faint('Enter query: ')}").strip()


def prompt_search_type(types: tuple[str, ...]) -> str:
    """Render the type screen and read a valid search type. Re-prompts on
    unrecognised input. `types` is the tuple of selectable type labels:

        │ downloader vX | Search
        ╰ Search type (track, album, etc.):
    """
    _erase_pending()
    _emit(header("Search"))
    prompt = f"{MARK_CLOSE} {faint('Search type (track, album, etc.): ')}"
    while True:
        raw = _read(prompt).strip().lower()
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
    _erase_pending()
    _emit(header(_search_title(type_label)))
    for i, fields in enumerate(rows, 1):
        _emit(_search_row(fields, i))
    n = len(rows)
    prompt = f"{MARK_CLOSE} {faint(f'Select [1-{n}] to download, or Q to quit: ')}"
    while True:
        raw = _read(prompt).strip().lower()
        if raw in ("q", ""):
            return None
        if raw.isdigit() and 1 <= int(raw) <= n:
            return int(raw) - 1
