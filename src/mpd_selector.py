import curses

class MPDStreamSelector:
    def __init__(self, representations: list):
        self.representations = representations

    def _menu(self, stdscr):
        curses.curs_set(0)
        current_row = 0

        while True:
            stdscr.clear()

            stdscr.addstr(
                0, 0, "select a stream (↑/↓, enter to select, p to pick, q to quit)")
            stdscr.addstr(1, 0, "-" * 80)

            for idx, rep in enumerate(self.representations):
                bitrate_kbps = rep["bandwidth"] // 1000

                line = (
                    f"[{rep['id']}] "
                    f"{rep['track_type']} | "
                    f"{rep['codec']} | "
                    f"{bitrate_kbps} kbps | "
                    f"{rep['sample_rate']} Hz"
                )

                if rep["bit_depth"]:
                    line += f" | {rep['bit_depth']}-bit"

                if idx == current_row:
                    stdscr.addstr(idx + 2, 0, line, curses.A_REVERSE)
                else:
                    stdscr.addstr(idx + 2, 0, line)

            key = stdscr.getch()

            if key == curses.KEY_UP:
                current_row = (current_row - 1) % len(self.representations)
            elif key == curses.KEY_DOWN:
                current_row = (current_row + 1) % len(self.representations)
            elif key == ord("\n"):
                return {
                    "base_url": self.representations[current_row]["base_url"],
                    "pssh": self.representations[current_row]["pssh"]
                }
            elif key == ord("p"):
                curses.endwin()
                return {
                    "base_url": self.representations[current_row]["base_url"],
                    "pssh": self.representations[current_row]["pssh"]
                }
            elif key == ord("q"):
                return None

            stdscr.refresh()

    def select(self):
        if not self.representations:
            raise ValueError("No playable representations found.")

        return curses.wrapper(self._menu)
