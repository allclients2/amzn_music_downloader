"""Resolve a download argument — a bare ASIN, an Amazon Music link, or a text file
of either — to the list of content ids the pipeline runs over.

The pipeline auto-detects each id's kind (track / album / artist / catalog-playlist
/ user-playlist) downstream in `fetch_metadata`, so a link only needs to yield its
content id. Link parsing mirrors the submodule's `custom_url_parse` (the `trackAsin`
query param wins; otherwise the path segment after the type keyword), reimplemented
standalone here so it needs no instantiated OrpheusDL interface.

The link's domain is otherwise ignored for id extraction, but its `<tld>` does name
the region (`co.jp` → JP) — see `LinkHint` / `hint`.

Handled link shapes (any `music.amazon.<tld>` domain):

    …/albums/<asin>?…&trackAsin=<track_asin>   -> the track asin (trackAsin wins)
    …/albums/<asin>                            -> album asin
    …/tracks/<asin>                            -> track asin
    …/artists/<asin>                           -> artist asin
    …/playlists/<asin>                         -> catalog-playlist asin
    …/my/playlists/<uuid>                      -> user-playlist id
    …/user-playlists/<id>                      -> user-playlist id
"""

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from amazonmusic.models import AmazonRegion

# An Amazon content ASIN is 10 alphanumeric chars (usually B0…). Only used to spot
# a bare ASIN token inside a URL path as a last resort — never to reject input.
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

# Path keywords Amazon Music uses (mirrored from the submodule's url_constants). The
# content id is the path segment immediately after one of these.
_TYPE_KEYWORDS = ("tracks", "albums", "playlists", "user-playlists", "artists")

# Which content type each path keyword is guaranteed to denote (drives `type_hint`).
# Catalog playlists (`/playlists/`) and user/library playlists (`/user-playlists/`,
# `/my/playlists/`) get distinct hints so `fetch_metadata` can try the right endpoint
# first (see `type_hint` for the `/my/` special case).
_KEYWORD_KIND = {
    "tracks": "track",
    "albums": "album",
    "artists": "artist",
    "playlists": "playlist",
    "user-playlists": "user-playlist",
}


def _id_from_url(link: str) -> str:
    """Extract the content id (ASIN or user-playlist id) from an Amazon Music link."""
    url = urlparse(link)
    # `?trackAsin=` selects one track inside an album/playlist URL — it wins.
    track = parse_qs(url.query).get("trackAsin")
    if track and track[0]:
        return track[0]
    components = [c for c in url.path.split("/") if c]
    for i, comp in enumerate(components):
        if comp in _TYPE_KEYWORDS and i + 1 < len(components):
            return components[i + 1]
    # No recognizable type keyword — fall back to the first ASIN-looking segment.
    for comp in components:
        if _ASIN_RE.match(comp):
            return comp
    raise ValueError(f"could not find a content id in link: {link}")


def _looks_like_url(item: str) -> bool:
    return "://" in item or "amazon." in item.lower()


# A few Amazon Music TLDs are shared by many regions (e.g. `com` covers the US plus
# most of Latin America and the smaller EU markets); resolve those to their primary
# music marketplace. Unambiguous TLDs (`co.jp`, `co.uk`, `fr`, …) need no override.
_TLD_PRIMARY = {"com": "US", "de": "DE", "com.au": "AU"}


@functools.lru_cache
def _tld_to_country() -> dict:
    """Reverse the submodule's region table into `{domain_tld: country}`, picking the
    primary marketplace for TLDs several regions share (`_TLD_PRIMARY`)."""
    by_tld: dict = {}
    for country, region in AmazonRegion.get_known_regions().items():
        by_tld.setdefault(region.domain_tld, []).append(country)
    return {
        tld: (_TLD_PRIMARY.get(tld, countries[0]) if len(countries) > 1 else countries[0])
        for tld, countries in by_tld.items()
    }


def _country_from_host(host: str) -> Optional[str]:
    """The 2-letter region implied by a `music.amazon.<tld>` host (`co.jp` → 'JP'),
    or None when the host isn't an Amazon domain or its TLD isn't recognized."""
    host = (host or "").lower()
    marker = "amazon."
    idx = host.rfind(marker)
    if idx == -1:
        return None
    return _tld_to_country().get(host[idx + len(marker):])


def _type_from_url(url) -> Optional[str]:
    """The content type a parsed link is guaranteed to resolve to, or None."""
    if parse_qs(url.query).get("trackAsin"):
        return "track"
    components = [c for c in url.path.split("/") if c]
    for i, comp in enumerate(components):
        if comp in _TYPE_KEYWORDS and i + 1 < len(components):
            # `/my/playlists/<id>` is a user/library playlist, not a catalog one.
            if comp == "playlists" and i > 0 and components[i - 1] == "my":
                return "user-playlist"
            return _KEYWORD_KIND.get(comp)
    return None


@dataclass(frozen=True)
class LinkHint:
    """What a single download argument reveals about itself up front, purely from a
    link's shape — no network. Either field is None when the link doesn't reveal it
    (or the input is a bare ASIN / text-file path). One object so new up-front signals
    can be added here without threading another parameter through the call sites.

    - `type`: the content type the input is *guaranteed* to resolve to — 'track' /
      'album' / 'artist' / 'playlist' / 'user-playlist'. The download fast path uses a
      'track' hint to fire the track-only manifest/lyrics calls concurrently with
      metadata; a 'playlist' / 'user-playlist' hint lets `fetch_metadata` skip the
      doomed muse lookup and hit the catalog vs user/library endpoint the shape names
      first; anything else resolves the type from metadata first.
    - `country`: the 2-letter region implied by the link's domain
      (`music.amazon.co.jp` → 'JP'), used to auto-pick the matching stored account.
    """
    type: Optional[str] = None
    country: Optional[str] = None


def hint(raw: str) -> LinkHint:
    """The `LinkHint` derived from a single download argument (see `LinkHint`)."""
    item = raw.strip()
    if not _looks_like_url(item):
        return LinkHint()
    try:
        url = urlparse(item)
    except ValueError:
        return LinkHint()
    return LinkHint(type=_type_from_url(url), country=_country_from_host(url.hostname or ""))


def type_hint(raw: str) -> Optional[str]:
    """Back-compat shorthand for `hint(raw).type` (see `LinkHint`)."""
    return hint(raw).type


def parse_one(item: str) -> str:
    """A single entry — a bare ASIN or an Amazon Music link — to its content id."""
    item = item.strip()
    if _looks_like_url(item):
        return _id_from_url(item)
    return item


def input_file_label(raw: str) -> Optional[str]:
    """The basename of `raw` if it points at an existing text file, else None.

    Lets the CLI tell a batch file apart from a bare ASIN/link and use the file's
    name as the batch progress header.
    """
    try:
        if Path(raw).is_file():
            return Path(raw).name
    except OSError:  # e.g. an over-long URL passed where a path was expected
        return None
    return None


def resolve_inputs(raw: str) -> List[str]:
    """Resolve a download argument to one or more content ids.

    `raw` is a bare ASIN, an Amazon Music link, or a path to a text file holding any
    mix of those, one per line (blank lines and `#` comments are ignored).
    """
    try:
        is_file = Path(raw).is_file()
    except OSError:  # e.g. an over-long URL passed where a path was expected
        is_file = False
    if is_file:
        lines = Path(raw).read_text(encoding="utf-8").splitlines()
        items = [ln.strip() for ln in lines]
        items = [ln for ln in items if ln and not ln.startswith("#")]
        return [parse_one(ln) for ln in items]
    return [parse_one(raw)]
