import requests
from bs4 import BeautifulSoup
import re


class Metadata:
    @staticmethod
    def getTrackMetadataFromEmbedLink(trackAsin: str) -> dict:
        url = f"https://music.amazon.co.jp/embed/{trackAsin}"
        response = requests.get(url)
        soup = BeautifulSoup(response.text, "html.parser")

        track_name = soup.select_one(".trackTitle a").get_text(strip=True)
        artist_name = soup.select_one(".trackArtist a").get_text(strip=True)
        album_title = soup.select_one("#ALBUM_TITLE")["value"]

        album_asin_input = soup.select_one("#ALBUM_ASIN")
        album_asin = album_asin_input["value"] if album_asin_input else None

        img_tag = soup.select_one(".headerImg img")
        artwork_url = img_tag["src"] if img_tag else None

        if artwork_url:
            artwork_url = Metadata.upgrade_amazon_image(artwork_url)

        return {
            "track_name": track_name,
            "artist_name": artist_name,
            "album_title": album_title,
            "album_asin": album_asin,
            "artwork_url": artwork_url
        }

    @staticmethod
    def upgrade_amazon_image(url: str) -> str:
        return re.sub(r"\._S[XY]\d+_", "._SL1200_", url)