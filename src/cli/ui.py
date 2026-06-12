"""Console UI kit — the single source of truth for the CLI's look.

Every interactive surface (the download progress bar, the account selector, the
`login` prompts, the OAuth paste screen, error messages) shares one visual
language: a faint-grey "tree" of connectors (``│ ├ ╰``), a cyan ``downloader vX``
brand, a faint-grey-bold ``|`` separator, and a few semantic accents (green
``done`` in `progress.py`, red ``×`` for errors).

This module owns the palette, the tree markers, the one-screen-at-a-time
bookkeeping, and the bar-aware log handler. The static wizard screens that build on
it live in `prompts.py`; the raw long-line terminal input lives in `terminal.py`;
`progress.py` builds the animated download bar on the same palette/markers. Keeping
the styling in one place is what keeps them all consistent.

Only one screen is shown at a time: each screen first rewinds over and clears
whatever the previous one printed (tracked in `_pending_rows`), the same
`\033[nF\033[J` trick `progress.py` uses for its own block — so the wizard reads as
one header replacing the next rather than a growing stack. `progress.py` calls
`consume_pending_screen()` on its first paint so the download bar likewise replaces
the last setup screen. Only our own output is ever cleared; the shell prompt above
the first screen is left untouched.

Color is emitted only to a real terminal: `paint()` returns plain text when stdout
is not a TTY (piped output), so escape codes never leak into logs.
"""

import logging
import re
import shutil
import sys

from _version import VERSION
from util import disp_width


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
    width = disp_width(_ANSI_RE.sub("", text))
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
