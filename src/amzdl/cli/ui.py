"""Console UI kit — the single source of truth for the CLI's look.

Every interactive surface (the download progress bar, the account selector, the
`login` prompts, the OAuth paste screen, error messages) shares one visual
language: a faint-grey "tree" of connectors (``│ ├ ╰``), a cyan ``amzdl vX``
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

Beneath each screen's prompt is a persistent footer (`_footer_rows`) of collected
messages: sticky `warn`/`error` alerts (yellow/red — never cleared, so they're
still on screen at exit for diagnosis) over transient `note` lines (faint — each
survives one screen transition). The root log handler funnels WARNING/ERROR records
into the same alert footer on interactive runs (see `_BarAwareHandler`).

Color is emitted only to a real terminal: `paint()` returns plain text when stdout
is not a TTY (piped output), so escape codes never leak into logs.
"""

import logging
import re
import shutil
import sys

from amzdl._version import VERSION
from amzdl.util import disp_width


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
BRAND_TEXT = f"amzdl v{VERSION}"   # raw (for width math); BRAND is the painted form

BRAND = paint(BRAND_TEXT, CYAN)
SEP = paint("|", FAINT, GREY, BOLD)     # header separator, rendered as f" {SEP} "

MARK_HEADER = faint("│")                # header line
MARK_TEE = paint("├", FAINT, GREY, BOLD)   # a body line with more below it
MARK_CLOSE = paint("╰", FAINT, GREY, BOLD)  # the closing/last line of a block


def header(*parts: str) -> str:
    """The header line: ``│ amzdl vX | part | part…``.

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

# ── persistent footer (notes + alerts) ───────────────────────────────────────
# Below every screen's prompt sits a footer of accumulated messages. It has two
# regions, sticky alerts first then transient notes:
#   • _alerts  — (level, text) pairs from `warn`/`error` and the log funnel.
#     Painted yellow (WARNING) / red (ERROR). NEVER cleared: they stay on screen
#     through every screen change and remain visible when the program exits, so
#     the user can diagnose what went wrong.
#   • notes    — faint incidental lines from `note`. Transient: a note survives
#     exactly one screen transition (it's shown on the screen drawn right after it
#     was added, then cleared on the next). Implemented as two lists — a note lands
#     in `_pending_notes`, is promoted to `_displayed_notes` when the next screen is
#     drawn, and is dropped when the screen after that is drawn.
# `_footer_rows` tracks the physical rows the footer currently occupies so it can
# be rewound independently of the screen body above it. The whole mechanism only
# engages on a TTY and only when the footer is non-empty: with nothing to show,
# screens print and read exactly as they did before (no cursor gymnastics).
_footer_rows = 0
_alerts: list[tuple[int, str]] = []
_displayed_notes: list[str] = []
_pending_notes: list[str] = []

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
    """Rewind over the previously printed screen *and its footer* and clear it.

    Also advances the transient-notes generation: notes added since the last screen
    was drawn (`_pending_notes`) become this screen's displayed notes, and the
    previous screen's displayed notes are dropped — so a note lives for exactly one
    screen. Sticky alerts are left untouched (they never clear)."""
    global _pending_rows, _footer_rows, _displayed_notes, _pending_notes
    total = _pending_rows + _footer_rows
    if _TTY and total:
        sys.stdout.write(f"\033[{total}F\033[J")
        sys.stdout.flush()
    _pending_rows = 0
    _footer_rows = 0
    _displayed_notes, _pending_notes = _pending_notes, []


def _emit(line: str) -> None:
    """Print one screen line, tracking the physical rows it occupies."""
    global _pending_rows
    print(line)
    _pending_rows += _vis_rows(line, _term_cols())


# ── footer rendering ──────────────────────────────────────────────────────────
def _footer_lines() -> list[str]:
    """The footer as painted lines: sticky alerts (yellow/red) then transient
    notes (faint), aligned under the alerts' one-char marker."""
    lines = []
    for level, text in _alerts:
        if level >= logging.ERROR:
            lines.append(f"{paint('×', RED)} {paint(text, RED)}")
        else:
            lines.append(f"{paint('⚠', YELLOW)} {paint(text, YELLOW)}")
    for text in (*_displayed_notes, *_pending_notes):
        lines.append(f"  {faint(text)}")
    return lines


def _repaint_footer() -> None:
    """Redraw the footer in place beneath the current screen, assuming the cursor
    sits just below the previous footer (the bottom of the screen). Used when a
    `note`/`warn`/`error` arrives *between* prompts (the in-prompt footer is drawn
    by `_read`). Leaves the cursor below the new footer. No-op off a TTY."""
    global _footer_rows
    if not _TTY:
        return
    lines = _footer_lines()
    if _footer_rows:
        # Rewind to the top of the old footer and clear from there down.
        sys.stdout.write(f"\033[{_footer_rows}F\033[J")
    else:
        # Cursor is on a fresh line below the screen; clear it and anything under.
        sys.stdout.write("\r\033[J")
    cols = _term_cols()
    rows = 0
    for line in lines:
        print(line)
        rows += _vis_rows(line, cols)
    sys.stdout.flush()
    _footer_rows = rows


def _read(prompt: str, reader=input, echo: bool = True) -> str:
    """Print `prompt`, read a line, and track the rows it occupied. With `echo`
    (the default, for `input`), the typed text stays on the prompt line until the
    trailing newline, so both are counted. With `echo=False` (the `read_long_line`
    paste, which suppresses its echo) only the prompt row is on screen, so only the
    prompt is counted — counting the unshown input would over-rewind the next erase.

    When the footer is non-empty it is drawn *below* the prompt line: the footer is
    printed first, the cursor is rewound up to the (still-blank) prompt line for the
    reader to use, and stepped back below the footer once input is submitted — so the
    rewind machinery still finds the bottom. (A typed line long enough to wrap would
    push into the footer; menu picks are short and the long paste suppresses echo.)"""
    global _pending_rows, _footer_rows
    cols = _term_cols()
    lines = _footer_lines()
    if not (_TTY and lines):
        # No footer to show — behave exactly as before.
        result = reader(prompt)
        _pending_rows += _vis_rows(prompt + result if echo else prompt, cols)
        return result

    prompt_rows = max(1, _vis_rows(_ANSI_RE.sub("", prompt), cols))
    n_footer = sum(_vis_rows(line, cols) for line in lines)
    # Reserve the prompt's rows (blank for now), print the footer beneath them,
    # then rewind to the top of the prompt so the reader prints/reads there.
    sys.stdout.write("\n" * prompt_rows)
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.write(f"\033[{prompt_rows + n_footer}A")
    sys.stdout.flush()
    result = reader(prompt)
    # Input's trailing newline left the cursor at the top of the footer; step below.
    sys.stdout.write(f"\033[{n_footer}B\r")
    sys.stdout.flush()
    _pending_rows += _vis_rows(prompt + result if echo else prompt, cols)
    _footer_rows = n_footer
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
    """Routes log records into the sticky-alert footer, buffering them while a
    progress bar owns the screen and flushing once the bar releases it.

    In `footer` mode (the default, interactive runs) a record becomes a footer
    alert coloured by level — WARNING yellow, ERROR/CRITICAL red — so warnings and
    errors collect beneath the screen and survive to program exit. In non-footer
    mode (verbose runs, or piped/non-TTY output) it falls back to a plain stderr
    `StreamHandler` so the raw chronological log stream is preserved."""

    def __init__(self):
        super().__init__()  # default stream: sys.stderr
        self._buffering = False
        self._buffer = []
        self._footer = False

    def emit(self, record):
        # logging calls this under `self.lock`, so the buffer access is serialized
        # with begin_bar/end_bar and concurrent emits from worker threads.
        if self._buffering:
            self._buffer.append(record)
            return
        self._deliver(record)

    def _deliver(self, record):
        if self._footer:
            _record_alert(record.levelno, record.getMessage())
            _repaint_footer()
        else:
            super().emit(record)

    def begin_bar(self):
        with self.lock:
            self._buffering = True

    def end_bar(self):
        with self.lock:
            self._buffering = False
            buffered, self._buffer = self._buffer, []
            if self._footer:
                # Coalesce the whole buffered batch into one footer repaint so the
                # alerts land cleanly beneath the finished bar.
                for record in buffered:
                    _record_alert(record.levelno, record.getMessage())
                if buffered:
                    _repaint_footer()
            else:
                for record in buffered:
                    super().emit(record)


def setup_logging(verbose: bool) -> None:
    """Install the bar-aware handler on the root logger (replaces `basicConfig`).

    All records — the project's `amzdl.*` loggers plus any third-party
    (pywidevine/urllib3/asyncio) loggers that propagate to root — flow through one
    handler. On an interactive (non-verbose, TTY) run they collect in the sticky
    footer; verbose or piped runs stream them to stderr live instead."""
    global _log_handler
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.WARNING)
    if _log_handler is None:
        _log_handler = _BarAwareHandler()
        _log_handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        root.addHandler(_log_handler)
    # Funnel into the footer only on an interactive terminal that isn't verbose;
    # otherwise keep the live stderr stream debuggers/log pipes expect.
    _log_handler._footer = _TTY and not verbose


def begin_bar_logging() -> None:
    """Start buffering log output (called when an animated bar takes the screen)."""
    if _log_handler is not None:
        _log_handler.begin_bar()


def end_bar_logging() -> None:
    """Stop buffering and flush whatever accumulated while the bar was live."""
    if _log_handler is not None:
        _log_handler.end_bar()


# ── footer messages (notes + alerts) ─────────────────────────────────────────
def note(text: str) -> None:
    """Add one faint-grey informational line to the footer (incidental output).

    Transient: shown beneath the current and next screen, then cleared. Off a TTY
    it just prints inline (no footer machinery)."""
    if not _TTY:
        print(faint(text))
        return
    _pending_notes.append(text)
    _repaint_footer()


def _record_alert(level: int, text: str) -> None:
    """Append a sticky alert (no repaint). Coloured by `level` when rendered."""
    _alerts.append((level, text))


def warn(text: str) -> None:
    """Add a sticky yellow warning to the footer. Never cleared; visible at exit."""
    if not _TTY:
        print(paint(text, YELLOW))
        return
    _record_alert(logging.WARNING, text)
    _repaint_footer()


def error(text: str) -> None:
    """Add a sticky red error to the footer. Never cleared; visible at exit.

    This is the non-fatal footer accent; `print_error` is the full-screen fatal
    error rendered just before the program exits."""
    if not _TTY:
        print(paint(text, RED))
        return
    _record_alert(logging.ERROR, text)
    _repaint_footer()


# ── screens ──────────────────────────────────────────────────────────────────
def print_error(message: str) -> None:
    """Render the error screen, with any accumulated footer alerts beneath it:

        │ amzdl vX | Error
        ╰ × <message>
    """
    _erase_pending()
    _emit(header("Error"))
    _emit(f"{MARK_CLOSE} {paint('×', RED)} {message}")
    _repaint_footer()
