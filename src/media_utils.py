import subprocess;
import json;
import math;
from pathlib import Path;


class MediaUtils:

    @staticmethod
    def get_duration_seconds(file_path: str) -> int:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path
        ];

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        );

        if result.returncode != 0:
            raise RuntimeError("ffprobe failed: " + result.stderr);

        data = json.loads(result.stdout);
        duration = float(data["format"]["duration"]);

        return int(math.ceil(duration));