"""Catalog search via the `textsearch` API.

Resolves a free-text query to a short list of `SearchResult`s (an ASIN plus the
display columns the picker shows). The CLI feeds the chosen result's ASIN straight
into the normal download pipeline (`metadata.fetch_metadata` -> ...). Every catalog
type that pipeline resolves is offered here — a single **track**, a full **album**,
an **artist** (whole discography) or a (catalog) **playlist**.

The underlying `AmazonMusicMobileAPI.search()` (the same `textsearch` endpoint
`metadata._hi_res_cover` uses) returns a tuple of raw document dicts when no `asins`
filter is given; the field names below come from those documents
(`com.amazon.music.platform.model#CatalogTrack` / `#CatalogAlbum` /
`#CatalogArtist` / `#CatalogPlaylist`). The shapes differ: a track/album/playlist
names itself in `title`, but an **artist** document uses `name` (and carries no
`artistName`); a playlist exposes a `trackCount` rather than an artist.
"""

from dataclasses import dataclass
from typing import List, Tuple

from auth.amzn_api import AmazonMusicMobileAPI

# Display label -> textsearch catalog type. Order is the menu/listing order.
SEARCH_TYPES = {
    "track": "catalog_track",
    "album": "catalog_album",
    "artist": "catalog_artist",
    "playlist": "catalog_playlist",
}


@dataclass
class SearchResult:
    """One catalog hit: the ASIN to download plus the columns the picker renders."""
    asin: str
    fields: Tuple[str, ...]


def normalize_type(value: str) -> str:
    """Map user input (case-insensitive, optional trailing 's') to a known type.

    Raises `ValueError` for anything outside `SEARCH_TYPES`."""
    norm = (value or "").strip().lower()
    if norm.endswith("s") and norm[:-1] in SEARCH_TYPES:
        norm = norm[:-1]
    if norm not in SEARCH_TYPES:
        raise ValueError(
            f"unknown search type '{value}' (choose one of: {', '.join(SEARCH_TYPES)})"
        )
    return norm


def _fields(search_type: str, doc: dict) -> Tuple[str, ...]:
    """The display columns for one hit, matching the picker's row format."""
    title = doc.get("title") or "?"
    artist = doc.get("artistName") or "?"
    if search_type == "track":
        # Track Name - Album Name - Artist Name
        return (title, doc.get("albumName") or "?", artist)
    if search_type == "album":
        # Album Name - Artist Name
        return (title, artist)
    if search_type == "artist":
        # Artist documents name themselves in `name`, not `title`.
        return (doc.get("name") or doc.get("title") or "?",)
    # playlist: Playlist Name - N tracks (no artist; trackCount stands in).
    count = doc.get("trackCount")
    return (title, f"{count} tracks") if count else (title,)


def search_catalog(
    session: AmazonMusicMobileAPI, query: str, search_type: str, limit: int
) -> List[SearchResult]:
    """Run the query and return up to `limit` downloadable results."""
    label = SEARCH_TYPES[normalize_type(search_type)]
    region = session.credentials.account_region
    # asins=None -> the endpoint returns a tuple of documents (or {} when empty).
    docs = session.search(
        query=query, search_types=(label,), limit=limit, region_to_use=region
    )

    results: List[SearchResult] = []
    for doc in docs or ():
        if not isinstance(doc, dict):
            continue
        asin = doc.get("asin")
        if not asin:
            continue
        results.append(SearchResult(asin=str(asin), fields=_fields(search_type, doc)))
        if len(results) >= limit:
            break
    return results
