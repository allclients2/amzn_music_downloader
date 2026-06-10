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

# Amazon labels each AdaptationSet with an `amz-music:trackType` of just the
# *umbrella* tier — `LD`, `SD`, `HD`, `UHD` (the linear ladder) or `3D` (spatial) —
# and expresses finer steps as multiple Representations at different bandwidths.
# The submodule README's granular names (`SD_HIGH`, `HD_44`, `SPATIAL_ATMOS_HIGH`,
# `SPATIAL_RA360_L2`, …) are OrpheusDL *settings* strings, not manifest labels, so
# we accept that vocabulary as input and resolve it against the real streams:
#
#  • LINEAR (LD<SD<HD<UHD, Opus up to SD, FLAC for HD/UHD): `default_quality` is a
#    ceiling. The `_LOW/_MEDIUM/_HIGH` suffix selects a within-tier bandwidth step;
#    `HD_44`/`UHD_48`/a bare tier mean the top of that tier.
#  • SPATIAL (`trackType == 3D`): the codec says which kind — Dolby Atmos (E-AC-3,
#    AC-3, AC-4) vs Sony 360RA / MPEG-H (MHA1, MHM1). Selected on its own axis; the
#    `SPATIAL_ATMOS_*` / `SPATIAL_RA360_L*` suffix picks a bandwidth step.
_LINEAR_TIER_RANK = {"LD": 0, "SD": 1, "HD": 2, "UHD": 3}
_SUB_LEVEL = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}   # within-tier step for *_LOW/_MEDIUM/_HIGH
_DEFAULT_QUALITY = "HD"

# Spatial codecs (lowercased codec prefix) grouped by family.
_ATMOS_CODECS = ("ec-3", "ec3", "eac3", "ac-3", "ac3", "ac-4", "ac4")
_RA360_CODECS = ("mha1", "mhm1", "mha", "mhm")

_AUDIO_EXTENSIONS = (".flac", ".opus", ".mp4", ".ac4")


def _is_linear_rep(rep) -> bool:
    return str(rep.get("track_type") or "").upper() in _LINEAR_TIER_RANK


def _spatial_family(rep):
    """'ATMOS' / 'RA360' for a spatial representation (by codec), else None."""
    codec = str(rep.get("codec") or "").lower()
    if codec.startswith(_ATMOS_CODECS):
        return "ATMOS"
    if codec.startswith(_RA360_CODECS):
        return "RA360"
    return None


def _parse_quality(quality):
    """Resolve a requested quality string to ``(kind, tier, level)``.

    kind  — 'LINEAR' | 'ATMOS' | 'RA360', or None if unrecognized.
    tier  — linear tier name (LD/SD/HD/UHD) for LINEAR, else None.
    level — within-group bandwidth step (0-based), or None meaning 'top of group'.
    """
    q = str(quality or "").upper()
    if q.startswith("SPATIAL_ATMOS"):
        return "ATMOS", None, _SUB_LEVEL.get(q[len("SPATIAL_ATMOS"):].lstrip("_"))
    if q.startswith("SPATIAL_RA360"):
        suffix = q[len("SPATIAL_RA360"):].lstrip("_")
        level = int(suffix[1:]) if suffix[:1] == "L" and suffix[1:].isdigit() else None
        return "RA360", None, level
    tier, _, sub = q.partition("_")
    if tier in _LINEAR_TIER_RANK:
        return "LINEAR", tier, _SUB_LEVEL.get(sub)   # HD_44/UHD_48/bare tier -> None (top)
    return None, None, None


def is_spatial_quality(quality) -> bool:
    """True for any Dolby Atmos / Sony 360RA tier (needs the 3D manifest variant)."""
    return _parse_quality(quality)[0] in ("ATMOS", "RA360")

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


def _fetch_manifest_xml(session, track_asin: str, force_3d: bool = False) -> str:
    region = session.credentials.account_region
    responses = session._get_tracks_manifest((track_asin,), region, force_3d or None)
    if not responses:
        raise ValueError(f"no manifest returned for {track_asin}")
    return responses[0]["manifest"]


def fetch_representations(session, track_asin: str, quality=None) -> list:
    """Fetch + parse the DASH manifest into a list of representations (network).

    Spatial streams (Atmos/360RA) appear only in Amazon's 3D manifest variant
    (`try3dAsinSubstitution`), which in turn drops UHD — so the 3D manifest is
    requested only when a spatial tier was asked for; everything else uses the
    normal manifest carrying the full LD…UHD ladder.
    """
    _log.debug("fetching track MPD streams for %s", track_asin)
    manifest_xml = _fetch_manifest_xml(session, track_asin, is_spatial_quality(quality))
    return parse_mpd(manifest_xml)


def _select_linear(representations: list, ceiling_tier: str, ceiling_level):
    """Best LINEAR stream at or below the `(ceiling_tier, ceiling_level)` ceiling.

    Reps are ranked by `(tier, bandwidth step within tier)`; the best one not
    exceeding the ceiling is returned (or the lowest available if the ceiling sits
    below every tier on offer). Spatial reps live on a different axis and are
    ignored here so the linear ceiling can never pick them."""
    linear = [r for r in representations if _is_linear_rep(r)]
    if not linear:
        return None

    # Per-tier bandwidth step (0 = lowest bitrate in that tier).
    step = {}
    for tier in {str(r["track_type"]).upper() for r in linear}:
        same = sorted((r for r in linear if str(r["track_type"]).upper() == tier),
                      key=lambda r: int(r["bandwidth"]))
        for i, r in enumerate(same):
            step[id(r)] = i

    def grank(r):
        return (_LINEAR_TIER_RANK[str(r["track_type"]).upper()], step[id(r)])

    ceiling_tr = _LINEAR_TIER_RANK[ceiling_tier]
    ceiling_step = 99 if ceiling_level is None else ceiling_level
    eligible = [
        r for r in linear
        if grank(r)[0] < ceiling_tr
        or (grank(r)[0] == ceiling_tr and grank(r)[1] <= ceiling_step)
    ]
    # Ceiling below the lowest tier present: fall back to the lowest stream on offer.
    return max(eligible or linear, key=grank)


def _select_spatial(representations: list, family: str, level):
    """Best stream of a spatial `family` ('ATMOS'/'RA360') at or below `level`
    (a bandwidth step; None = top), or None if the track has no such stream."""
    cands = sorted(
        (r for r in representations if _spatial_family(r) == family),
        key=lambda r: int(r["bandwidth"]),
    )
    if not cands:
        return None
    return cands[-1] if level is None else cands[min(level, len(cands) - 1)]


def select_representation(track_asin: str, representations: list, quality):
    """Pick the best representation for the requested `quality`.

    A linear tier acts as a ceiling (best FLAC/Opus stream at or below it). A spatial
    tier selects the best stream of that family (Atmos / 360RA) at or below the
    requested level; if the track carries no such spatial stream, fall back to the
    best lossless FLAC so the download still succeeds (as a non-spatial file)."""
    if not representations:
        _log.warning("no available representations for %s", track_asin)
        return None

    kind, tier, level = _parse_quality(quality)
    if kind is None:
        _log.warning("unknown quality %r; falling back to %s", quality, _DEFAULT_QUALITY)
        kind, tier, level = "LINEAR", _DEFAULT_QUALITY, None

    if kind == "LINEAR":
        rep = _select_linear(representations, tier, level)
    else:
        rep = _select_spatial(representations, kind, level)
        if rep is None:
            _log.warning("no %s stream for %s; falling back to best FLAC", kind, track_asin)
            rep = _select_linear(representations, "UHD", None)

    if rep is None:
        _log.warning("no playable representation for %s", track_asin)
        return None

    return TrackRepresentation(
        track_asin=track_asin,
        mpd_representation=rep,
        quality=quality,
    )


def find_representation(session, track_asin: str, quality) -> TrackRepresentation:
    """Fetch the manifest and select a representation (used by the album path)."""
    reps = fetch_representations(session, track_asin, quality)
    return select_representation(track_asin, reps, quality)


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
