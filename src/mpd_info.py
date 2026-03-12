# com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getLicenseForPlaybackV2

import xml.etree.ElementTree as ET
import requests
import uuid
from html import unescape
from mpd_selector import MPDStreamSelector
from dataclasses import dataclass

@dataclass
class TrackRepresentation:
    track_asin: str
    mpd_representation: list
    min_bitrate: str

def find_representation(
    track_asin: str,
    config,
    cookie_header: str,
    min_bitrate: str
) -> TrackRepresentation:
    print("fetching track MPD streams...")
    try:
        manifest_xml = fetchTrackMpdInfo(track_asin, config, cookie_header)
        representations: list = parse_mpd(manifest_xml)
    except KeyError:
        print("failed to fetch MPD streams. try reloading the website or refetching cookies.")

    if not representations:
        print("no available representations.")
        return
    
    selector = MPDStreamSelector(representations)

    if min_bitrate == "max":
        rep = max(representations, key=lambda r: int(r["bandwidth"]))
    elif min_bitrate == "min":
        rep = min(representations, key=lambda r: int(r["bandwidth"]))
    elif min_bitrate and min_bitrate.isdigit():
        eligible = [
            r for r in representations
            if int(r["bandwidth"]) // 1000 >= min_bitrate
        ]
        if not eligible:
            rep = max(representations, key=lambda r: int(r["bandwidth"]))
        else:
            rep = min(eligible, key=lambda r: int(r["bandwidth"]))
    else:
        result = selector.select()

        if not result:
            print("no track selection made.")
            return
        
        rep = next(
            r for r in selector.representations
            if r["base_url"] == result["base_url"]
        )
    return TrackRepresentation(
        track_asin=track_asin,
        mpd_representation=rep,
        min_bitrate=min_bitrate
    )

def parse_mpd(raw_xml: str):
    root = ET.fromstring(raw_xml)

    ns = {
        "mpd": "urn:mpeg:dash:schema:mpd:2011",
        "cenc": "urn:mpeg:cenc:2013"
    }

    representations = []

    for adaptation in root.findall(".//mpd:AdaptationSet", ns):
        track_type = None
        adaptation_pssh = None

        # Track type
        for prop in adaptation.findall("mpd:SupplementalProperty", ns):
            if prop.attrib.get("schemeIdUri") == "amz-music:trackType":
                track_type = prop.attrib.get("value")

        # Extract PSSH from ContentProtection
        for cp in adaptation.findall("mpd:ContentProtection", ns):
            pssh_elem = cp.find("cenc:pssh", ns)
            if pssh_elem is not None and pssh_elem.text:
                adaptation_pssh = pssh_elem.text.strip()
                break

        for rep in adaptation.findall("mpd:Representation", ns):
            base_url = rep.find("mpd:BaseURL", ns)
            seglist = rep.find("mpd:SegmentList", ns)

            if base_url is None or seglist is None:
                continue

            segments = seglist.findall("mpd:SegmentURL", ns)

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
                    segments[0].attrib.get(
                        "mediaRange") if segments else None
                ),
                "pssh": adaptation_pssh
            })

    return representations

def fetchTrackMpdInfo(contentAsin: str, config, cookieHeader: str) -> str:
    url = "https://music.amazon.co.jp/FE/api/dmls/"

    headers = {
        "Authorization": "Bearer " + config["accessToken"],
        "Cookie": cookieHeader,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
        "X-Amz-Target": "com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getDashManifestsV2",
        "Csrf-Token": config["csrf"]["token"],
        "Csrf-Rnd": config["csrf"]["rnd"],
        "Csrf-Ts": config["csrf"]["ts"],
        "Content-Encoding": "amz-1.0",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://music.amazon.co.jp"
    }

    payload = {
        "deviceToken": {
            "deviceTypeId": config["deviceType"],
            "deviceId": config["deviceId"]
        },
        "appMetadata": {
            "https": "true"
        },
        "clientMetadata": {
            "clientId": "WebCP",
            "clientRequestId": str(uuid.uuid4())
        },
        "customerId": config["customerId"],
        "contentIdList": [
            {
                "identifier": contentAsin, # e.g.: "B0CBVKPZ68"
                "identifierType": "ASIN"
            }
        ],
        "musicDashVersionList": [
            "SIREN_KATANA"
        ],
        "contentProtectionList": [
            "TRACK_PSSH"
        ],
        "tryAsinSubstitution": "true",
        "customerInfo": {
            "marketplaceId": config["marketplaceId"],
            "territoryId": config["musicTerritory"]
        },
        "appInfo": {
            "musicAgent": "Maestro/1.0 WebCP/1.0.9527.0 (d12c-cb21-WebC-d25b-627dd)"
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload
    )

    if response.status_code != 200:
        print("status code:", response.status_code)
        print("response text:", response.text)

    return response.json()["contentResponseList"][0]["manifest"]


