import xml.etree.ElementTree as ET;
import curses;
import requests;
import subprocess;
from html import unescape;

class MPDStreamSelector:
    def __init__(self, raw_xml: str):
        self.raw_xml = raw_xml;
        self.representations = self._parse_mpd();

    def _parse_mpd(self):
        root = ET.fromstring(self.raw_xml);

        ns = {
            "mpd": "urn:mpeg:dash:schema:mpd:2011",
            "cenc": "urn:mpeg:cenc:2013"
        };

        representations = [];

        for adaptation in root.findall(".//mpd:AdaptationSet", ns):
            track_type = None;
            adaptation_pssh = None;

            # Track type
            for prop in adaptation.findall("mpd:SupplementalProperty", ns):
                if prop.attrib.get("schemeIdUri") == "amz-music:trackType":
                    track_type = prop.attrib.get("value");

            # Extract PSSH from ContentProtection
            for cp in adaptation.findall("mpd:ContentProtection", ns):
                pssh_elem = cp.find("cenc:pssh", ns);
                if pssh_elem is not None and pssh_elem.text:
                    adaptation_pssh = pssh_elem.text.strip();
                    break;

            for rep in adaptation.findall("mpd:Representation", ns):
                base_url = rep.find("mpd:BaseURL", ns);
                seglist = rep.find("mpd:SegmentList", ns);

                if base_url is None or seglist is None:
                    continue;

                segments = seglist.findall("mpd:SegmentURL", ns);

                representations.append({
                    "id": rep.attrib.get("id"),
                    "track_type": track_type,
                    "codec": rep.attrib.get("codecs"),
                    "bandwidth": int(rep.attrib.get("bandwidth", 0)),
                    "sample_rate": rep.attrib.get("audioSamplingRate"),
                    "bit_depth": next(
                        (
                            sp.attrib.get("value")
                            for sp in rep.findall("mpd:SupplementalProperty", ns)
                            if sp.attrib.get("schemeIdUri") == "amz-music:bitDepth"
                        ),
                        None
                    ),
                    "base_url": unescape(base_url.text.strip()),
                    "first_segment_range": (
                        segments[0].attrib.get("mediaRange") if segments else None
                    ),
                    "pssh": adaptation_pssh
                });

        return representations;

    def download_full_file(self, rep, output_path=None):
        if output_path is None:
            output_path = f"{rep['id']}_full.bin";

        r = requests.get(rep["base_url"], stream=True);

        if r.status_code != 200:
            print(f"Download failed. Status code: {r.status_code}");
            return None;

        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk);

        print(f"Full file saved to: {output_path}");
        return output_path;

    def _menu(self, stdscr):
        curses.curs_set(0);
        current_row = 0;

        while True:
            stdscr.clear();

            stdscr.addstr(0, 0, "Select a stream (↑/↓, Enter to select, p to pick, q to quit)");
            stdscr.addstr(1, 0, "-" * 80);

            for idx, rep in enumerate(self.representations):
                bitrate_kbps = rep["bandwidth"] // 1000;

                line = (
                    f"[{rep['id']}] "
                    f"{rep['track_type']} | "
                    f"{rep['codec']} | "
                    f"{bitrate_kbps} kbps | "
                    f"{rep['sample_rate']} Hz"
                );

                if rep["bit_depth"]:
                    line += f" | {rep['bit_depth']}-bit";

                if idx == current_row:
                    stdscr.addstr(idx + 2, 0, line, curses.A_REVERSE);
                else:
                    stdscr.addstr(idx + 2, 0, line);

            key = stdscr.getch();

            if key == curses.KEY_UP:
                current_row = (current_row - 1) % len(self.representations);
            elif key == curses.KEY_DOWN:
                current_row = (current_row + 1) % len(self.representations);
            elif key == ord("\n"):
                return {
                    "base_url": self.representations[current_row]["base_url"],
                    "pssh": self.representations[current_row]["pssh"]
                };
            elif key == ord("p"):
                curses.endwin();
                return {
                    "base_url": self.representations[current_row]["base_url"],
                    "pssh": self.representations[current_row]["pssh"]
                };
            elif key == ord("q"):
                return None;

            stdscr.refresh();

    def select(self):
        if not self.representations:
            raise ValueError("No playable representations found.");

        return curses.wrapper(self._menu);