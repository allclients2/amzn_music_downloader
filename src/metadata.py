import requests
from bs4 import BeautifulSoup
import re
from dataclasses import dataclass
from typing import List, Union, Optional

@dataclass
class TrackMetadata:
    track_name: str
    artist_name: str
    album_name: str
    track_asin: str
    album_asin: str
    disc: int|None
    track_number: int|None

    def fetch_disc_info(self):
        if (self.disc is not None) and (self.track_number is not None):
            return

        album_metadata = Metadata.getMetadataFromEmbedLink(self.album_asin)

        disc_found = None
        track_number_found = None
        for track in album_metadata.tracks:
            if track.track_asin == self.track_asin:
                disc_found = track.disc
                track_number_found = track.track_number
                break

        self.disc = disc_found 
        self.track_number = track_number_found

        return album_metadata

@dataclass
class AlbumMetadata:
    album_name: str
    artist_name: str
    album_asin: str
    artwork_url: Optional[str]
    tracks: List[TrackMetadata]

@dataclass
class MetadataResult:
    album: AlbumMetadata
    track: Optional[TrackMetadata] = None

class Metadata:

    @staticmethod
    def getMetadataFromEmbedLink(contentAsin: str) -> Union[TrackMetadata, AlbumMetadata]:
        url = f"https://music.amazon.co.jp/embed/{contentAsin}"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")

        content_type = soup.select_one("#TYPE")["value"]

        if content_type == "track":
            return Metadata._parse_track(soup)

        if content_type == "album":
            return Metadata._parse_album(soup)

        raise ValueError("Unsupported content type")

    @staticmethod
    def _parse_track(soup: BeautifulSoup) -> tuple[TrackMetadata, AlbumMetadata]:
        track_name = soup.select_one(".trackTitle a").get_text(strip=True)
        artist_name = soup.select_one(".trackArtist a").get_text(strip=True)
        album_name = soup.select_one("#ALBUM_TITLE")["value"]

        album_asin_input = soup.select_one("#ALBUM_ASIN")
        album_asin = album_asin_input["value"] if album_asin_input else None

        assert album_asin, "album_asin not found"

        track_asin_input = soup.select_one("#ASIN")
        track_asin = track_asin_input["value"] if track_asin_input else None

        return TrackMetadata(
            track_name=track_name,
            artist_name=artist_name,
            album_name=album_name,
            album_asin=album_asin,
            track_asin=track_asin,
            disc=None,
            track_number=None
        )

    @staticmethod
    def _parse_album(soup: BeautifulSoup) -> AlbumMetadata:
        album_name = soup.select_one("#contentTitle").get_text(strip=True)
        album_artist_name = soup.select_one(".headerMetaData a").get_text(strip=True)
        album_asin = soup.select_one("#ASIN")["value"]

        img_tag = soup.select_one(".albumHeader img")
        artwork_url = img_tag["src"] if img_tag else None

        if artwork_url:
            artwork_url = Metadata.upgrade_amazon_image(artwork_url)

        tracks: List[TrackMetadata] = []
        current_disc = 1

        for element in soup.select("#tracksContainer > li"):

            if "albumDiscValue" in element.get("class", []):
                disc_text = element.get_text(strip=True)
                match = re.search(r"(\d+)", disc_text)
                if match:
                    current_disc = int(match.group(1))
                continue

            if "trackItem" in element.get("class", []):
                track_asin_input = element.select_one(".trackAsin")
                title_link = element.select_one(".trackListTitle a")
                artist_link = element.select_one(".trackListArtist a")
                index_number = element.select_one(".indexNumber")

                if not (track_asin_input and title_link and artist_link and index_number):
                    continue

                tracks.append(
                    TrackMetadata(
                        track_name=title_link.get_text(strip=True),
                        artist_name=artist_link.get_text(strip=True),
                        album_name=album_name,
                        track_asin=track_asin_input["value"],
                        album_asin=album_asin,
                        disc=current_disc,
                        track_number=int(index_number.get_text(strip=True)),
                    )
                )

        return AlbumMetadata(
            album_name=album_name,
            artist_name=album_artist_name,
            album_asin=album_asin,
            artwork_url=artwork_url,
            tracks=tracks
        )

    @staticmethod
    def upgrade_amazon_image(url: str) -> str:
        return re.sub(r"\._S[XY]\d+_", "._SL1200_", url)