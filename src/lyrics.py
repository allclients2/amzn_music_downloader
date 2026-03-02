
import json;
import uuid;
import requests;
import time;


class Lyrics:
    @staticmethod
    def _ms_to_lrc_timestamp(ms: int):
        total_seconds = ms // 1000;
        minutes = total_seconds // 60;
        seconds = total_seconds % 60;
        hundredths = (ms % 1000) // 10;

        return f"{minutes:02}:{seconds:02}.{hundredths:02}";

    @staticmethod
    def convert_lyrics_to_lrc(lyrics_json: dict):
        methods = lyrics_json.get("methods", []);
        lyrics_block = None;

        for method in methods:
            if method.get("interface", "").endswith("SetLyricsMethod"):
                lyrics_block = method.get("lyrics");
                break;

        if not lyrics_block:
            raise ValueError("No lyrics found.");

        lines = lyrics_block["lines"];
        timing = lyrics_block["timing"];

        lrc_lines = [];
        last_index = None;

        for ms_str in sorted(timing.keys(), key=lambda x: int(x)):
            index = timing[ms_str];

            if index != last_index:
                text = lines.get(index, "").strip();

                if text:
                    timestamp = Lyrics._ms_to_lrc_timestamp(int(ms_str));
                    lrc_lines.append(f"[{timestamp}]{text}");

                last_index = index;

        return "\n".join(lrc_lines);

    @staticmethod
    def save_lrc(lyrics_json: dict, output_path="output.lrc"):
        content = Lyrics.convert_lyrics_to_lrc(lyrics_json)

        if not content:
            return

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        return output_path;

    @staticmethod
    def fetch_lyrics(track_asin: str, duration: int, config: dict):
        url = "https://fe.web.skill.music.a2z.com/api/showLyrics";

        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "Origin": "https://music.amazon.co.jp",
            "Referer": "https://music.amazon.co.jp/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        }

        auth_obj = {
            "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
            "accessToken": config["accessToken"]
        }

        csrf_obj = {
            "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
            "token": config["csrf"]["token"],
            "timestamp": config["csrf"]["ts"],
            "rndNonce": config["csrf"]["rnd"]
        }

        headers_obj = {
            "x-amzn-authentication": json.dumps(auth_obj),
            "x-amzn-device-model": "WEBPLAYER",
            "x-amzn-device-width": "1920",
            "x-amzn-device-family": "WebPlayer",
            "x-amzn-device-id": config["deviceId"],
            "x-amzn-user-agent": "Mozilla/5.0",
            "x-amzn-session-id": config.get("sessionId", ""),
            "x-amzn-device-height": "1080",
            "x-amzn-request-id": str(uuid.uuid4()),
            "x-amzn-device-language": "en_US",
            "x-amzn-currency-of-preference": config.get("currency", "JPY"),
            "x-amzn-os-version": "1.0",
            "x-amzn-application-version": "1.0.9527.0",
            "x-amzn-device-time-zone": config.get("timezone", "America/New_York"),
            "x-amzn-timestamp": str(int(time.time() * 1000)),
            "x-amzn-csrf": json.dumps(csrf_obj),
            "x-amzn-music-domain": config.get("musicDomain", "music.amazon.co.jp"),
            "x-amzn-feature-flags": "hd-supported,uhd-supported",
            "x-amzn-has-profile-id": "true",
            "x-amzn-age-band": "",
            "x-amzn-referer": "music.amazon.co.jp",
            "x-amzn-affiliate-tags": "",
            "x-amzn-ref-marker": "",
            "x-amzn-page-url": f"https://music.amazon.co.jp/albums/{track_asin}",
            "x-amzn-weblab-id-overrides": "",
        }

        payload = {
            "durationSeconds": str(duration),
            "id": track_asin,
            "isLibrary": "false",
            "userHash": json.dumps({"level": "HD_MEMBER"}),
            "headers": json.dumps(headers_obj)
        }

        response = requests.post(url, headers=headers, json=payload)

        response.raise_for_status()
        return response.json()