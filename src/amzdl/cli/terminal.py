"""Raw single-line terminal input for the CLI.

Amazon's post-login OAuth URLs are far longer than a terminal's canonical line
buffer, so `read_long_line` reads them raw (ICANON/ECHO off) and acknowledges the
paste with a live ``[N chars]`` counter instead of echoing the URL — keeping the
prompt a single, non-scrolling line so `ui`'s one-screen-at-a-time redraw stays in
sync. The raw-read backend is platform-specific (`termios` on POSIX, `msvcrt` on
Windows); `read_long_line` dispatches on whichever is importable.
"""

import sys

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
