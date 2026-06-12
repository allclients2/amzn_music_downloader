"""Artist discography discovery via submodule endpoints (no catalog search).

An artist's album ASINs are harvested from its `get_page("artist/<asin>")` catalog
page (popular/new releases) plus the paginated `chronological-albums` shoveler. The
page payload models each album as a dict tagged with a `__type` ending in `#Album`,
whose `asin` is the album ASIN. `build_artist` returns the `ArtistMetadata` the
download pipeline then expands per album ASIN.
"""

from typing import List, Optional

from amazonmusic.azapi import AmazonMusicMobileAPI

from metadata.metadata import ArtistMetadata

# Album ASINs are discovered from the artist's catalog pages (`get_page`), never
# from textsearch. The page payload models each album as a dict tagged with a
# `__type` of `...brush#Album`, whose `asin` is the album ASIN.
_ALBUM_TYPE_SUFFIX = "#Album"
_ARTIST_PAGE_MAX_PAGES = 40   # hard stop on discography pagination


def _find_next_token(obj) -> Optional[str]:
    """The pagination `nextToken` anywhere in a get_page payload, or None."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, value in cur.items():
                lk = str(key).lower()
                if "next" in lk and "token" in lk and value:
                    return str(value)
                if isinstance(value, (dict, list, tuple)):
                    stack.append(value)
        elif isinstance(cur, (list, tuple)):
            stack.extend(cur)
    return None


def _collect_album_asins(obj, seen: set, ordered: List[str]) -> None:
    """Append (in encounter order, de-duped) the ASIN of every album entity — a
    dict whose `__type` ends in `#Album` — found anywhere in a get_page payload."""
    if isinstance(obj, dict):
        if str(obj.get("__type", "")).endswith(_ALBUM_TYPE_SUFFIX) and obj.get("asin"):
            asin = str(obj["asin"])
            if asin not in seen:
                seen.add(asin)
                ordered.append(asin)
        for value in obj.values():
            if isinstance(value, (dict, list, tuple)):
                _collect_album_asins(value, seen, ordered)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_album_asins(item, seen, ordered)


def _artist_album_asins(session: AmazonMusicMobileAPI, artist_asin: str) -> List[str]:
    """Every album ASIN in an artist's discography, de-duped, with no catalog search.

    The artist landing page (`artist/<asin>`) surfaces the popular/new-release
    albums up front, then the `chronological-albums` shoveler is paginated (via its
    `nextToken`) for the complete discography.
    """
    seen: set = set()
    ordered: List[str] = []

    try:
        _collect_album_asins(session.get_page(f"artist/{artist_asin}"), seen, ordered)
    except Exception:
        pass

    next_token = None
    for _ in range(_ARTIST_PAGE_MAX_PAGES):
        try:
            page = session.get_page(
                f"artist/{artist_asin}/chronological-albums", next_token=next_token
            )
        except Exception:
            break
        _collect_album_asins(page, seen, ordered)
        next_token = _find_next_token(page)
        if not next_token:
            break
    return ordered


def build_artist(
    session: AmazonMusicMobileAPI, artist_data: dict, content_asin: str
) -> ArtistMetadata:
    artist_asin = str(artist_data.get("asin") or content_asin)
    return ArtistMetadata(
        name=artist_data.get("name") or "Unknown Artist",
        asin=artist_asin,
        album_asins=_artist_album_asins(session, artist_asin),
    )
