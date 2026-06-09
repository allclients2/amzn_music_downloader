import shutil
import sys
import threading
import time
import unicodedata

VERSION = "2.0"

# ── ANSI styling ────────────────────────────────────────────────────────────
_BOLD = "\033[1m"
_FAINT = "\033[2m"
_GREY = "\033[37m"
_CYAN = "\033[34m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


def _paint(text: str, *codes: str) -> str:
    """Wrap `text` in the given ANSI codes, resetting afterwards."""
    return f"{''.join(codes)}{text}{_RESET}"


_FILL = "█"
_BRAND_TEXT = f"downloader v{VERSION}"

# Tree-connector markers printed at the start of the two lines. They're faint +
# colored and currently decorative — reserved for grouping multiple downloads.
_HEADER_MARK = "│"
_BAR_MARK = "╰"

# Pre-composed, state-independent line pieces (built once, never reformatted).
_MARK_HEADER = _paint(_HEADER_MARK, _FAINT, _GREY)
_MARK_BAR = _paint(_BAR_MARK, _FAINT, _GREY, _BOLD)
_SEP = _paint("|", _FAINT, _GREY, _BOLD)   # rendered as f" {_SEP} "
_BRAND = _paint(_BRAND_TEXT, _CYAN)
_DONE = _paint("done", _GREEN)

# Output occupies this fraction of the terminal width (leaves breathing room on
# the right); the longest description shown in the bar suffix.
_WIDTH_FRAC = 0.9
_DESC_MAX = 16

# How fast the title marquee scrolls (display columns per second) and how often
# the ticker thread redraws to animate it.
_MARQUEE_CPS = 6
_TICK_INTERVAL = 0.12


def _char_width(c: str) -> int:
    return 2 if unicodedata.east_asian_width(c) in ("W", "F") else 1


def _disp_width(s: str) -> int:
    """Display width, counting CJK/full-width characters as 2 columns."""
    return sum(_char_width(c) for c in s)


def _take_cols(s: str, max_width: int) -> str:
    """Leading slice of `s` that fits in `max_width` columns (no ellipsis)."""
    if max_width <= 0:
        return ""
    out, width = "", 0
    for c in s:
        cw = _char_width(c)
        if width + cw > max_width:
            break
        out += c
        width += cw
    return out


def _truncate(s: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _disp_width(s) <= max_width:
        return s
    out, width = "", 0
    for c in s:
        cw = _char_width(c)
        if width + cw > max_width - 1:
            break
        out += c
        width += cw
    return out + "…"


class Progress:
    def __init__(self, asin: str, total: int, unit: str = "steps", plain: bool = False):
        self.asin = asin
        self.total = max(1, total)
        self.unit = unit
        self.n = 0
        self.name = None
        self.desc = ""
        self.done = False
        self.start = time.time()
        # `plain` (e.g. verbose mode) disables the in-place redraw so log output
        # doesn't fight the bar.
        self._tty = sys.stdout.isatty() and not plain
        self._rendered = False
        # The ticker thread and event-driven updates can both redraw; serialize
        # their stdout writes.
        self._lock = threading.Lock()
        self._stop = False
        self._ticker = None
        if self._tty:
            # Animate the marquee between state changes.
            self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
            self._ticker.start()

    # ── public API ──────────────────────────────────────────────────────────
    def reconfigure(self, total: int, unit: str):
        """Switch scale once the content type is known (e.g. steps -> tracks)."""
        self.total = max(1, total)
        self.unit = unit
        self.n = 0
        self.start = time.time()
        self.render()

    def set_name(self, name: str):
        self.name = name
        self.render()

    def set_desc(self, desc: str):
        self.desc = desc
        self.render()

    def update(self, desc: str = None, advance: int = 1):
        self.n += advance
        if desc is not None:
            self.desc = desc
        self.render()

    def finish(self, desc: str = None):
        self.done = True
        self.n = self.total
        if desc is not None:
            self.desc = desc
        self._shutdown_ticker()
        self.render()  # ends on a fresh line below the bar (trailing newline)

    def abort(self):
        """The traceback prints cleanly: the last render already left the cursor
        on the line below the bar."""
        self._shutdown_ticker()

    # ── ticker ────────────────────────────────────────────────────────────
    def _tick_loop(self):
        while not self._stop:
            time.sleep(_TICK_INTERVAL)
            if not self._stop:
                self.render()

    def _shutdown_ticker(self):
        self._stop = True
        if self._ticker is not None and self._ticker is not threading.current_thread():
            self._ticker.join(timeout=_TICK_INTERVAL * 2)

    # ── rendering ─────────────────────────────────────────────────────────
    @staticmethod
    def _term_width() -> int:
        cols = shutil.get_terminal_size((100, 24)).columns
        return min(int(cols * _WIDTH_FRAC), 70)

    def _marquee(self, text: str, width: int) -> str:
        """Scrolling window over `text`; returns full text if it already fits."""
        if width <= 0 or not text:
            return ""
        if _disp_width(text) <= width:
            return text
        cycle = text + "   "  # trailing gap so the wrap-around reads cleanly
        pos = int((time.time() - self.start) * _MARQUEE_CPS) % len(cycle)
        rolled = cycle[pos:] + cycle[:pos]
        # Repeat until we can fill the whole window across the wrap-around.
        while _disp_width(rolled) < width:
            rolled += cycle
        return _take_cols(rolled, width)

    def _title(self, term_w: int) -> str:
        """Header track/album name: marquee while running, truncate when done."""
        if not self.name:
            return ""
        # Columns left after "│ downloader vX │ <asin> │ " (each " | " is 3 cols).
        prefix_w = 2 + _disp_width(_BRAND_TEXT) + 3 + _disp_width(self.asin) + 3
        avail = term_w - prefix_w
        return _truncate(self.name, avail) if self.done else self._marquee(self.name, avail)

    def _header(self, term_w: int) -> str:
        parts = [_BRAND, _paint(self.asin, _YELLOW)]
        title = self._title(term_w)
        if title:
            parts.append(title)
        return f"{_MARK_HEADER} " + f" {_SEP} ".join(parts)

    def render(self):
        term_w = self._term_width()
        frac = 1.0 if self.done else min(1.0, self.n / self.total)
        pct = f"{int(frac * 100):>3}%"
        desc = "done" if self.done else _truncate(self.desc, _DESC_MAX)

        # bar line: "╰ " + pct + " " + bar + " " + desc. Reserve a *fixed*
        # _DESC_MAX-wide slot for the description (not its current length) so the
        # bar width stays constant — otherwise a longer track title would shrink
        # the bar and make progress look like it went backwards.
        overhead = _disp_width(_BAR_MARK) + 1 + len(pct) + 1 + 1 + _DESC_MAX
        bar_w = max(10, term_w - overhead)
        filled = int(bar_w * frac)

        if not self._tty:
            # Non-interactive: emit a plain one-liner per change (no ANSI in logs).
            label = "done" if self.done else pct
            tail = self.name or self.asin if self.done else _truncate(self.desc, _DESC_MAX)
            print(f"{_HEADER_MARK} {label} | {tail}")
            return

        bar = _paint(_FILL * filled + " " * (bar_w - filled), _FAINT)
        suffix = _DONE if self.done else desc
        line1 = self._header(term_w)
        line2 = f"{_MARK_BAR} {pct} {bar} {suffix}"

        with self._lock:
            if self._stop and not self.done:
                # Aborted by another thread between checks; don't draw over the error.
                return
            if self._rendered:
                # The block is 3 lines tall (2 content lines + the parked cursor
                # line below); rewind to the header line and clear downwards.
                sys.stdout.write("\033[2F\033[J")
            sys.stdout.write(f"{line1}\n{line2}\n")
            sys.stdout.flush()
            self._rendered = True
