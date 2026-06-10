import shutil
import sys
import threading
import time
import unicodedata

import ui
from ui import paint as _paint
from ui import FAINT as _FAINT, GREY as _GREY, YELLOW as _YELLOW, GREEN as _GREEN

# ── ANSI styling ────────────────────────────────────────────────────────────
# The palette, brand, separator, and shared tree markers live in `ui` (one source
# of truth for the whole CLI's look); progress just composes them.

_FILL = "█"
_BRAND_TEXT = ui.BRAND_TEXT   # raw text, for the header width math below

# Tree-connector markers. header "│", album aggregate "├", per-track slots
# "├─" (and "╰─" for the last one), single-track bar "╰". The raw single-char
# glyphs are kept for width math + the plain (non-TTY) renderer.
_HEADER_MARK = "│"
_AGG_MARK = "├"
_SLOT_MARK = "├─"
_SLOT_MARK_LAST = "╰─"
_BAR_MARK = "╰"

# Pre-composed, state-independent line pieces (built once, never reformatted).
# Header/aggregate/bar markers + brand + separator come from `ui`; the two-char
# per-track slot connectors are progress-specific.
_MARK_HEADER = ui.MARK_HEADER
_MARK_AGG = ui.MARK_TEE
_MARK_SLOT = _paint(_SLOT_MARK, _FAINT, _GREY)
_MARK_SLOT_LAST = _paint(_SLOT_MARK_LAST, _FAINT, _GREY)
_MARK_BAR = ui.MARK_CLOSE
_SEP = ui.SEP   # rendered as f" {_SEP} "
_BRAND = ui.BRAND
_DONE = _paint("done", _GREEN)

# Output occupies this fraction of the terminal width (leaves breathing room on
# the right).
_WIDTH_FRAC = .8

# Per-track download is measured in these fixed stages (see fetch_track).
_SINGLE_STEPS = 5     # single-track step bar denominator
_TRACK_STEPS = 5      # album per-track slot bar denominator

# Fixed column reservations so bar widths never jitter with text length.
_DESC_MAX = 20        # single-track step description
_SLOT_NAME_W = 7      # truncated track name in a slot line
_SLOT_DESC_W = 18     # step description in a slot line

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


def _fixed(s: str, width: int) -> str:
    """Truncate `s` to `width` columns, then pad with spaces to exactly `width`."""
    t = _truncate(s, width)
    return t + " " * (width - _disp_width(t))


def _fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _bar(width: int, frac: float) -> str:
    filled = int(width * frac)
    return _paint(_FILL * filled + " " * (width - filled), _FAINT)


class _Slot:
    """One track's step progress within an album download. While it runs it
    occupies a slot line; once finished it's removed from the display (its
    position in the pool may be reused by the next track)."""
    __slots__ = ("name", "total", "n", "desc", "done")

    def __init__(self, name: str, total: int):
        self.name = name
        self.total = max(1, total)
        self.n = 0
        self.desc = ""
        self.done = False


class Progress:
    def __init__(self, asin: str, plain: bool = False, steps: int = _SINGLE_STEPS):
        self.asin = asin
        self.name = None
        self.desc = ""
        self.done = False
        self.start = time.time()

        # Single-track step bar.
        self.steps_total = max(1, steps)
        self.n = 0

        # Album (multi-track concurrent) state.
        self._album = False
        self._slots: list = []
        self.completed = 0
        self.track_total = 0
        self.album_start = None
        self._agg_reserve = 0

        # `plain` (e.g. verbose mode) disables the in-place redraw so log output
        # doesn't fight the bar.
        self._tty = sys.stdout.isatty() and not plain
        self._rendered = False
        self._rendered_lines = 0
        # The ticker thread and event-driven updates can both redraw; serialize
        # their stdout writes and slot reads/writes.
        self._lock = threading.Lock()
        self._stop = False
        self._ticker = None
        if self._tty:
            # Animate the marquee + elapsed/ETA between state changes.
            self._ticker = threading.Thread(target=self._tick_loop, daemon=True)
            self._ticker.start()

    # ── public API: single track ──────────────────────────────────────────
    def set_name(self, name: str):
        self.name = name
        self.render()

    def set_desc(self, desc: str):
        self.desc = desc
        self.render()

    def update(self, desc: str = None, advance: int = 1):
        """Advance the single-track step bar (used as the per-step on_step)."""
        self.n += advance
        if desc is not None:
            self.desc = desc
        self.render()

    # ── public API: album ───────────────────────────────────────────────────
    def begin_album(self, total: int):
        """Switch to the multi-track concurrent layout."""
        with self._lock:
            self._album = True
            self.track_total = max(1, total)
            self.completed = 0
            self.album_start = time.time()
            self._slots = []
            cw = len(str(self.track_total))
            sample = f"{self.track_total:>{cw}}/{self.track_total}  [00:00<00:00,  0.0 tracks/s]"
            self._agg_reserve = _disp_width(sample)
        self.render()

    def track_start(self, name: str, steps: int = _TRACK_STEPS) -> _Slot:
        """Begin a track; returns a handle for `track_step`/`track_done`."""
        slot = _Slot(name, steps)
        with self._lock:
            self.desc = name
            # Reuse the lowest finished (or empty) position so the slot lines
            # stay put; otherwise grow the pool.
            for i, s in enumerate(self._slots):
                if s is None or s.done:
                    self._slots[i] = slot
                    break
            else:
                self._slots.append(slot)
        self.render()
        return slot

    def track_step(self, slot: _Slot, desc: str):
        with self._lock:
            slot.n += 1
            slot.desc = desc
            self.desc = desc
        self.render()

    def track_done(self, slot: _Slot):
        # Mark the slot finished; it drops out of the rendered block (a later
        # track_start may reuse its pool position) so the display shrinks as
        # tracks complete.
        with self._lock:
            self.completed += 1
            slot.done = True
            slot.n = slot.total
        self.render()

    # ── public API: lifecycle ───────────────────────────────────────────────
    def finish(self, desc: str = None):
        self.done = True
        self.n = self.steps_total
        if self._album:
            self.completed = self.track_total
        if desc is not None:
            self.desc = desc
        self._shutdown_ticker()
        self.render()  # ends on a fresh line below the block (trailing newline)

    def abort(self):
        """Stop animating and hand the half-finished block off to `ui` so the
        error screen that follows replaces it (one screen at a time).

        In plain/verbose mode (no in-place block) nothing was rendered in place,
        so there's nothing to hand off: the last render already left the cursor on
        the line below the block and the traceback prints cleanly beneath it."""
        self._shutdown_ticker()
        if self._tty and self._rendered:
            # The block's lines are each one physical row (sized to fit the
            # terminal, never wrapped); register them so ui.print_error erases it.
            ui.adopt_pending_rows(self._rendered_lines)

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
        return max(40, int(cols * _WIDTH_FRAC))

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

    def _single_line(self, term_w: int) -> str:
        frac = 1.0 if self.done else min(1.0, self.n / self.steps_total)
        pct = f"{int(frac * 100):>3}%"
        # Reserve a fixed _DESC_MAX slot so the bar width stays constant.
        overhead = _disp_width(_BAR_MARK) + 1 + len(pct) + 1 + 1 + _DESC_MAX
        bar_w = max(10, term_w - overhead)
        suffix = _DONE if self.done else _paint(_truncate(self.desc, _DESC_MAX), _FAINT)
        return f"{_MARK_BAR} {pct} {_bar(bar_w, frac)} {suffix}"

    def _agg_frac(self) -> float:
        if self.track_total == 0:
            return 0.0
        active = sum(s.n / s.total for s in self._slots if s)
        return min(1.0, (self.completed + active) / self.track_total)

    def _agg_line(self, term_w: int, last: bool = False) -> str:
        # `last` when no track slots follow: close the tree with "╰" instead
        # of the "├" tee.
        mark = _MARK_BAR if last else _MARK_AGG
        frac = 1.0 if self.done else self._agg_frac()
        pct = f"{int(frac * 100):>3}%"
        if self.done:
            suffix = _DONE
        else:
            cw = len(str(self.track_total))
            count = f"{self.completed:>{cw}}/{self.track_total}"
            elapsed = time.time() - (self.album_start or self.start)
            rate = self.completed / elapsed if elapsed > 0 else 0.0
            eta = (self.track_total - self.completed) / rate if rate > 0 else 0.0
            suffix = _paint(
                f"{count}  [{_fmt_time(elapsed)}<{_fmt_time(eta)}, {rate:4.1f} tracks/s]",
                _FAINT,
            )
        # Reserve a fixed suffix slot so the aggregate bar width stays constant.
        overhead = _disp_width(_AGG_MARK) + 1 + len(pct) + 1 + 1 + self._agg_reserve
        bar_w = max(10, term_w - overhead)
        return f"{mark} {pct} {_bar(bar_w, frac)} {suffix}"

    def _slot_line(self, term_w: int, slot: _Slot, last: bool) -> str:
        mark = _MARK_SLOT_LAST if last else _MARK_SLOT
        name = _paint(_fixed(slot.name, _SLOT_NAME_W), _FAINT)
        if slot.done:
            frac = 1.0
            suffix = _DONE
        else:
            frac = min(1.0, slot.n / slot.total)
            suffix = _truncate(slot.desc, _SLOT_DESC_W)
        pct = f"{int(frac * 100):>3}%"
        # mark "├─"/"╰─" is 2 cols; name + pct + desc are fixed-width reservations.
        overhead = 2 + 1 + _SLOT_NAME_W + 1 + len(pct) + 1 + 1 + _SLOT_DESC_W
        bar_w = max(8, term_w - overhead)
        return f"{mark} {name} {pct} {_bar(bar_w, frac)} {suffix}"

    def _lines(self, term_w: int) -> list:
        lines = [self._header(term_w)]
        if self._album:
            # Only in-flight tracks get a slot line; finished ones are dropped so
            # the block shrinks as the album completes, ending on just the header
            # and aggregate line.
            active = [s for s in self._slots if s and not s.done]
            lines.append(self._agg_line(term_w, last=not active))
            for i, slot in enumerate(active):
                lines.append(self._slot_line(term_w, slot, last=(i == len(active) - 1)))
        else:
            lines.append(self._single_line(term_w))
        return lines

    def _render_plain(self):
        """Non-interactive: one plain line per change (no ANSI in logs)."""
        if self.done:
            print(f"{_HEADER_MARK} done | {self.name or self.asin}")
        elif self._album:
            pct = int(self._agg_frac() * 100)
            print(f"{_HEADER_MARK} {pct:>3}% | {self.completed}/{self.track_total} | {self.desc}")
        else:
            pct = int(min(1.0, self.n / self.steps_total) * 100)
            print(f"{_HEADER_MARK} {pct:>3}% | {_truncate(self.desc, _DESC_MAX)}")

    def render(self):
        if not self._tty:
            self._render_plain()
            return
        term_w = self._term_width()
        with self._lock:
            if self._stop and not self.done:
                # Aborted by another thread; don't draw over the error/traceback.
                return
            lines = self._lines(term_w)
            if self._rendered:
                # Rewind to the top of the previous block and clear downwards.
                sys.stdout.write(f"\033[{self._rendered_lines}F\033[J")
            else:
                # First paint: clear the last setup screen (account selector /
                # login notes) so the bar takes over the same single screen.
                ui.consume_pending_screen()
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            self._rendered_lines = len(lines)
            self._rendered = True
