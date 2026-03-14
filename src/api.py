import requests
import os

from pathlib import Path
from http.cookiejar import MozillaCookieJar

from configs import fetch_configs, build_browser_with_cookies
from cookies import Cookies, CookieError
from metadata import Metadata, TrackMetadata, AlbumMetadata
from metadata2 import Metadata2
from fetch_track import fetch_track
from mpd_info import find_representation


"""
# Metadata Classes Documentation

These objects are returned by `get_metadata()` and used by the downloader.

---

# TrackMetadata

Represents **basic track metadata extracted from embed pages**.

Fields:

```
track_name
artist_name
album_name
track_asin
album_asin
disc
track_number
```

Example:

```
TrackMetadata(
    track_name="Song",
    artist_name="Artist",
    album_name="Album",
    track_asin="B0TRACK",
    album_asin="B0ALBUM"
)
```

### fetch_disc_info()

Fetches missing disc / track numbers.

Used when embed metadata does not include them.

---

# AlbumMetadata

Represents album metadata from the embed page.

Fields:

```
album_name
artist_name
album_asin
artwork_url
tracks: List[TrackMetadata]
```

Each album contains a list of `TrackMetadata`.

---

# MetadataResult

Container returned by some metadata calls.

```
MetadataResult(
    album=AlbumMetadata,
    track=TrackMetadata | None
)
```

Used when requesting metadata for a **single track belonging to an album**.

---

# TrackMetadataV2

Metadata retrieved from **Amazon Music internal APIs**.

Contains richer data than V1.

Fields:

```
asin
title
artist
duration_seconds
is_explicit
lyrics_available
popularity
lyrics
```

Lyrics may be attached later with:

```
track.attach_lyrics(lyrics)
```

---

# AlbumMetadataV2

Represents full album metadata parsed from internal Amazon Music API responses.

Fields:

```
asin
name
artist
total_duration_seconds
release_date_iso
copyright
label
track_count
cover_art_url
background_image_url
tracks: List[TrackMetadataV2]
```

Created via:

```
AlbumMetadataV2.from_response()
```

---

# TrackRepresentation

Represents a **selected MPD stream variant**.

Fields:

```
track_asin
mpd_representation
min_bitrate
```

`mpd_representation` contains:

```
bandwidth
base_url
codec
```

Example:

```
TrackRepresentation(
    track_asin="B0TRACK",
    mpd_representation={
        "bandwidth": "320000",
        "base_url": "..."
    }
)
```

---

# Representation Selection

The function `find_representation()` chooses a stream variant from the MPD manifest.

Selection modes:

```
"max"   -> highest bitrate
"min"   -> lowest bitrate
number  -> minimum kbps threshold
None    -> interactive selector
```

Example:

```
rep = find_representation(
    track_asin="B0TRACK",
    config=config,
    cookie_header=cookie_header,
    min_bitrate="max"
)
```

---

# Example Full Workflow

```python
dl = Downloader(verbose=True)

await dl.initialize()

meta = await dl.get_metadata("B0TRACK")

reps = await dl.get_representations(meta["track"].track_asin)

await dl.download_track(
    representation=reps,
    metadata=meta
)
```
"""


class Downloader:

    """
    Amazon Music downloader API.

    This class manages authentication, metadata retrieval,
    stream selection, and downloading.

    Typical usage:

        dl = Downloader(cookies_file="cookies.txt")

        await dl.initialize()

        meta = await dl.get_metadata("ASIN")

        reps = await dl.get_representations(meta["track"].track_asin)

        await dl.download_track(
            representation=reps[0],
            metadata=meta
        )

    Metadata can be reused to avoid repeated API requests.
    """

    def __init__(
        self,
        cookies_file="cookies.txt",
        output_dir=".",
        build_folder_structure=False,
        min_bitrate=None,
        verbose=False
    ):

        self.cookies_file = cookies_file
        self.output_dir = Path(output_dir)
        self.min_bitrate = min_bitrate
        self.verbose = verbose
        self.build_folder_structure = build_folder_structure

        self.session = None
        self.jar = None
        self.browser = None
        self.config = None
        self.cookie_header = None

    def _log(self, *msg):

        if self.verbose:
            print(*msg)

    def _load_cookie_session(self):

        session = requests.Session()

        jar = MozillaCookieJar(self.cookies_file)

        if os.path.exists(self.cookies_file):
            jar.load(ignore_discard=True, ignore_expires=True)

        session.cookies = jar

        return session, jar

    def _cookie_header_to_jar(self, cookie_header, domain=".amazon.co.jp"):

        for pair in cookie_header.split(";"):

            name, value = pair.strip().split("=", 1)

            self.session.cookies.set(
                name,
                value,
                domain=domain,
                path="/"
            )

    async def initialize(self, from_browser=False, browser="chrome"):
        """
        Initialize the downloader.

        Must be called before metadata or downloads.

        Example:

            dl = Downloader()
            await dl.initialize(from_browser=True)
        """

        try:

            if from_browser:

                self._log("Loading cookies from browser")

                self.cookie_header = Cookies.from_browser(
                    domain=".amazon.co.jp",
                    browser=browser
                )

                self.session = requests.Session()

                self._cookie_header_to_jar(self.cookie_header)

                self.jar = None

            else:

                self.session, self.jar = self._load_cookie_session()

        except CookieError as e:

            raise RuntimeError(str(e))

        self._log("Launching browser")

        self.browser = build_browser_with_cookies(self.session)

        self._log("Fetching configs")

        self.config = fetch_configs(self.browser)

        if self.jar:

            self.jar.save(
                ignore_discard=False,
                ignore_expires=False
            )

    async def get_representations(
        self,
        track_asin,
        min_bitrate=None
    ):
        """
        Retrieve available MPD stream representations.

        Parameters
        ----------
        track_asin : str
            Track ASIN.

        min_bitrate : str|int|None
            Optional bitrate filter.

        Returns
        -------
        list[TrackRepresentation]

        Example
        -------
        reps = await dl.get_representations("B0TRACK")

        best = reps[-1]
        """

        representation = find_representation(
            track_asin=track_asin,
            config=self.config,
            cookie_header=self.cookie_header,
            min_bitrate=min_bitrate or self.min_bitrate
        )

        return representation

    async def get_metadata(self, asin):
        """
        Fetch metadata for a track or album.

        Returns a dictionary describing the content.

        Example:

            meta = await dl.get_metadata("B0ABC123")

            if meta["type"] == "track":
                print(meta["track"].track_name)

            if meta["type"] == "album":
                for t in meta["tracks"]:
                    print(t.track_name)
        """

        metadatav1 = Metadata.getMetadataFromEmbedLink(asin)

        album_metadatav2 = Metadata2.get_album_metadatav2(
            album_asin=metadatav1.album_asin,
            config=self.config
        )

        if isinstance(metadatav1, TrackMetadata):

            track_metadatav2 = next(
                (t for t in album_metadatav2.tracks if t.asin == metadatav1.track_asin),
                None
            )

            return {
                "type": "track",
                "track": metadatav1,
                "track_v2": track_metadatav2,
                "album_v2": album_metadatav2
            }

        if isinstance(metadatav1, AlbumMetadata):

            return {
                "type": "album",
                "album": metadatav1,
                "tracks": metadatav1.tracks,
                "album_v2": album_metadatav2
            }

    async def download_track(
        self,
        track_asin=None,
        representation=None,
        metadata=None,
        output_dir=None
    ):
        """
        Download a single track.

        Parameters
        ----------
        track_asin : str
            Track ASIN (optional if metadata provided)

        representation : TrackRepresentation
            Representation selected by the user.

        metadata : dict
            Metadata returned by get_metadata().

        output_dir : str|Path
            Optional override download directory.

        Notes
        -----
        If metadata is provided, it will not be refetched.
        """

        if not metadata:
            metadata = await self.get_metadata(track_asin)

        metadatav1 = metadata["track"]
        track_metadatav2 = metadata["track_v2"]
        album_metadatav2 = metadata["album_v2"]

        album_metadatav1 = metadatav1.fetch_disc_info()

        if not representation:

            representation = find_representation(
                track_asin=metadatav1.track_asin,
                config=self.config,
                cookie_header=self.cookie_header,
                min_bitrate=self.min_bitrate
            )

        await fetch_track(
            track_representation=representation,
            track_metadatav1=metadatav1,
            track_metadatav2=track_metadatav2,
            album_metadatav1=album_metadatav1,
            album_metadatav2=album_metadatav2,
            output_dir=output_dir or self.output_dir,
            config=self.config,
            cookie_header=self.cookie_header,
            build_folder_structure=self.build_folder_structure
        )

    async def download_album(
        self,
        album_asin=None,
        metadata=None,
        output_dir=None
    ):
        """
        Download an entire album.

        Parameters
        ----------
        album_asin : str
            Album ASIN.

        metadata : dict
            Metadata returned from get_metadata().

        output_dir : Path|str
            Optional custom output directory.
        """

        if not metadata:
            metadata = await self.get_metadata(album_asin)

        album_metadatav1 = metadata["album"]
        album_metadatav2 = metadata["album_v2"]

        for index, track_metadatav1 in enumerate(album_metadatav1.tracks):

            track_metadatav2 = album_metadatav2.tracks[index]

            representation = find_representation(
                track_asin=track_metadatav1.track_asin,
                config=self.config,
                cookie_header=self.cookie_header,
                min_bitrate=self.min_bitrate
            )

            await fetch_track(
                track_representation=representation,
                track_metadatav1=track_metadatav1,
                track_metadatav2=track_metadatav2,
                album_metadatav1=album_metadatav1,
                album_metadatav2=album_metadatav2,
                output_dir=output_dir or self.output_dir,
                config=self.config,
                cookie_header=self.cookie_header,
                build_folder_structure=self.build_folder_structure
            )

    async def close(self):
        """
        Shutdown Playwright browser.

        Call this when finished using the downloader.
        """

        if not self.browser:
            return

        self.browser["browser"].close()
        self.browser["playwright"].stop()
