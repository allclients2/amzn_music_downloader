"""DASH manifest retrieval + parsing.

The manifest now comes from the signed `getDashManifestsV2` endpoint via the
vendored `AmazonMusicMobileAPI` (replacing the old web-player `accessToken`/CSRF
request). The DASH XML it returns carries both a GROUP_PSSH (entitlement) and a
TRACK_PSSH (web playback). For the plain Widevine license path we use the
**web PSSH**: the ContentProtection whose Widevine `schemeIdUri` has no `value`
attribute (entitlement ones carry `value="AmzMusic-2019"`).
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape

from mpd_selector import MPDStreamSelector

_log = logging.getLogger("downloader.mpd")

# Widevine DRM system id.
_WIDEVINE_SCHEME = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"

_NS = {
    "mpd": "urn:mpeg:dash:schema:mpd:2011",
    "cenc": "urn:mpeg:cenc:2013",
}


@dataclass
class TrackRepresentation:
    track_asin: str
    mpd_representation: dict
    min_bitrate: str


def _fetch_manifest_xml(session, track_asin: str) -> str:
    region = session.credentials.account_region
    responses = session._get_tracks_manifest((track_asin,), region, None)
    if not responses:
        raise ValueError(f"no manifest returned for {track_asin}")
    return responses[0]["manifest"]


def fetch_representations(session, track_asin: str) -> list:
    """Fetch + parse the DASH manifest into a list of representations (network)."""
    _log.debug("fetching track MPD streams for %s", track_asin)
    manifest_xml = _fetch_manifest_xml(session, track_asin)
    return parse_mpd(manifest_xml)


def select_representation(track_asin: str, representations: list, min_bitrate):
    """Pick one representation by bitrate (or the interactive picker)."""
    if not representations:
        _log.warning("no available representations for %s", track_asin)
        return None

    if min_bitrate == "max":
        rep = max(representations, key=lambda r: int(r["bandwidth"]))
    elif min_bitrate == "min":
        rep = min(representations, key=lambda r: int(r["bandwidth"]))
    elif isinstance(min_bitrate, int):
        eligible = [r for r in representations if int(r["bandwidth"]) // 1000 >= min_bitrate]
        rep = (
            min(eligible, key=lambda r: int(r["bandwidth"]))
            if eligible
            else max(representations, key=lambda r: int(r["bandwidth"]))
        )
    else:
        selector = MPDStreamSelector(representations)
        result = selector.select()
        if not result:
            print("no track selection made.")
            return None
        rep = next(r for r in representations if r["base_url"] == result["base_url"])

    return TrackRepresentation(
        track_asin=track_asin,
        mpd_representation=rep,
        min_bitrate=min_bitrate,
    )


def find_representation(session, track_asin: str, min_bitrate) -> TrackRepresentation:
    """Fetch the manifest and select a representation (used by the album path)."""
    return select_representation(track_asin, fetch_representations(session, track_asin), min_bitrate)


def _web_pssh(adaptation) -> str:
    """Return the web-playback (TRACK_PSSH) cenc:pssh for an AdaptationSet, if any."""
    for cp in adaptation.findall("mpd:ContentProtection", _NS):
        if cp.attrib.get("schemeIdUri") != _WIDEVINE_SCHEME:
            continue
        # Entitlement ContentProtection carries value="AmzMusic-2019"; the web
        # one has no value attribute.
        if cp.attrib.get("value"):
            continue
        pssh_elem = cp.find("cenc:pssh", _NS)
        if pssh_elem is not None and pssh_elem.text:
            return pssh_elem.text.strip()
    return None


def parse_mpd(raw_xml: str):
    root = ET.fromstring(raw_xml)
    representations = []

    for adaptation in root.findall(".//mpd:AdaptationSet", _NS):
        track_type = None
        for prop in adaptation.findall("mpd:SupplementalProperty", _NS):
            if prop.attrib.get("schemeIdUri") == "amz-music:trackType":
                track_type = prop.attrib.get("value")

        adaptation_pssh = _web_pssh(adaptation)

        for rep in adaptation.findall("mpd:Representation", _NS):
            base_url = rep.find("mpd:BaseURL", _NS)
            if base_url is None or not base_url.text:
                # Amazon's on-demand profile serves the whole encrypted file from
                # BaseURL; reps without one are unusable.
                continue

            seglist = rep.find("mpd:SegmentList", _NS)
            segments = seglist.findall("mpd:SegmentURL", _NS) if seglist is not None else []

            representations.append({
                "id": rep.attrib.get("id"),
                "track_type": track_type,
                "codec": rep.attrib.get("codecs"),
                "bandwidth": int(rep.attrib.get("bandwidth", 0)),
                "sample_rate": rep.attrib.get("audioSamplingRate"),
                "bit_depth": next(
                    (
                        sp.attrib.get("value")
                        for sp in rep.findall("mpd:SupplementalProperty", _NS)
                        if sp.attrib.get("schemeIdUri") == "amz-music:bitDepth"
                    ),
                    None,
                ),
                "base_url": unescape(base_url.text.strip()),
                "first_segment_range": (
                    segments[0].attrib.get("mediaRange") if segments else None
                ),
                "pssh": adaptation_pssh,
            })

    return representations
