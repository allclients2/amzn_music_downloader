"""Resolve a download argument — a bare ASIN, an Amazon Music link, or a text file
of either — to the list of content ids the pipeline runs over.

The pipeline auto-detects each id's kind (track / album / artist / catalog-playlist
/ user-playlist) downstream in `fetch_metadata`, so a link only needs to yield its
content id. Link parsing mirrors the submodule's `custom_url_parse` (the `trackAsin`
query param wins; otherwise the path segment after the type keyword), reimplemented
standalone here so it needs no instantiated OrpheusDL interface.

Handled link shapes (any `music.amazon.<tld>` domain — the host is ignored):

    …/albums/<asin>?…&trackAsin=<track_asin>   -> the track asin (trackAsin wins)
    …/albums/<asin>                            -> album asin
    …/tracks/<asin>                            -> track asin
    …/artists/<asin>                           -> artist asin
    …/playlists/<asin>                         -> catalog-playlist asin
    …/my/playlists/<uuid>                      -> user-playlist id
    …/user-playlists/<id>                      -> user-playlist id
"""

import re
from pathlib import Path
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

# An Amazon content ASIN is 10 alphanumeric chars (usually B0…). Only used to spot
# a bare ASIN token inside a URL path as a last resort — never to reject input.
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

# Path keywords Amazon Music uses (mirrored from the submodule's url_constants). The
# content id is the path segment immediately after one of these.
_TYPE_KEYWORDS = ("tracks", "albums", "playlists", "user-playlists", "artists")


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
