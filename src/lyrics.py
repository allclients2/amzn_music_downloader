from dataclasses import dataclass
from typing import Dict, List


@dataclass
class LyricsLine:
    timestamp_ms: int
    text: str

@dataclass
class Lyrics:
    lines: List[LyricsLine]

    @staticmethod
    def from_json(lyrics_json: dict) -> "Lyrics":
        methods = lyrics_json.get("methods", [])
        lyrics_block = None

        for method in methods:
            if method.get("interface", "").endswith("SetLyricsMethod"):
                lyrics_block = method.get("lyrics")
                break

        if not lyrics_block:
            raise ValueError("No lyrics found in JSON.")

        raw_lines: Dict[str, str] = lyrics_block["lines"]
        timing: Dict[str, int] = lyrics_block["timing"]

        parsed_lines: List[LyricsLine] = []
        last_index = None

        for ms_str in sorted(timing.keys(), key=lambda x: int(x)):
            index = timing[ms_str]

            if index != last_index:
                text = raw_lines.get(str(index), "").strip()
                if text:
                    parsed_lines.append(
                        LyricsLine(
                            timestamp_ms=int(ms_str),
                            text=text
                        )
                    )
                last_index = index

        return Lyrics(lines=parsed_lines)

    @staticmethod
    def _ms_to_lrc_timestamp(ms: int) -> str:
        total_seconds = ms // 1000
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        hundredths = (ms % 1000) // 10
        return f"{minutes:02}:{seconds:02}.{hundredths:02}"

    def to_lrc(self) -> str:
        output = []
        for line in self.lines:
            timestamp = self._ms_to_lrc_timestamp(line.timestamp_ms)
            output.append(f"[{timestamp}]{line.text}")
        return "\n".join(output)

    def save_lrc(self, path: str) -> str:
        content = self.to_lrc()
        if not content:
            return path

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return path

    # unsynced lyrics suitable for MP4 tag '\xa9lyr'; omits timestamps.
    def to_mp4_lyrics(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def has_content(self) -> bool:
        return len(self.lines) > 0