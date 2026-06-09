"""DASH manifest retrieval, parsing, and stream selection.

Fetches the DASH manifest from the signed `getDashManifestsV2` endpoint, parses it
into a list of representations, and picks one by quality tier (see
`select_representation`).

The DASH XML carries two Widevine `ContentProtection` blocks: a GROUP_PSSH
(entitlement, marked `value="AmzMusic-2019"`) and a web TRACK_PSSH (no `value`).
The plain-license path uses the **web PSSH** — the one with no `value` attribute.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape

_log = logging.getLogger("downloader.mpd")

# Amazon's quality tiers, low → high. `default_quality` acts as a ceiling (like
# OrpheusDL's `max_track_quality_to_use`): pick the best stream that doesn't
# exceed it. SD = lossy opus, HD = CD-quality FLAC, UHD = hi-res FLAC.
_QUALITY_RANK = {"SD": 1, "HD": 2, "UHD": 3}
_DEFAULT_QUALITY = "HD"

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
    quality: str


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


def _rep_rank(rep: dict) -> int:
    """Quality-tier rank of a representation (unknown tiers sort below SD)."""
    return _QUALITY_RANK.get(str(rep.get("track_type") or "").upper(), 0)


def select_representation(track_asin: str, representations: list, quality):
    """Pick the best representation at or below the `quality` tier ceiling."""
    if not representations:
        _log.warning("no available representations for %s", track_asin)
        return None

    ceiling = _QUALITY_RANK.get(str(quality or "").upper())
    if ceiling is None:
        _log.warning("unknown quality %r; falling back to %s", quality, _DEFAULT_QUALITY)
        ceiling = _QUALITY_RANK[_DEFAULT_QUALITY]

    eligible = [r for r in representations if _rep_rank(r) <= ceiling]
    if eligible:
        # Best stream within the ceiling.
        rep = max(eligible, key=lambda r: int(r["bandwidth"]))
    else:
        # Everything is above the ceiling — fall back to the lowest tier on offer.
        lowest = min(_rep_rank(r) for r in representations)
        rep = max(
            (r for r in representations if _rep_rank(r) == lowest),
            key=lambda r: int(r["bandwidth"]),
        )

    return TrackRepresentation(
        track_asin=track_asin,
        mpd_representation=rep,
        quality=quality,
    )


def find_representation(session, track_asin: str, quality) -> TrackRepresentation:
    """Fetch the manifest and select a representation (used by the album path)."""
    return select_representation(track_asin, fetch_representations(session, track_asin), quality)


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
