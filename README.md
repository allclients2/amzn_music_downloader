# Amazon Music Downloader
This is vibecoded trash. I only made it because there isn't any other one out there.
Probably because they all got DMCA'd or something and this will too. Oh well.

> [!WARNING]
> Amazon.co.jp is extensively used in the code if you're from a different country please change it!

Usage:
Make sure `mp4decrypt` is in the system path
Get a device widevine file and name it as `device.wvd`. Place it into the directory of the repo (not src), then use as follows:

usage: main.py [-h] [--output-dir OUTPUT_DIR] [--cookies-file COOKIES_FILE] [-v] [--from-browser]
               [--browser {chrome,edge,firefox}] [--min-bitrate MIN_BITRATE]
               content_asin

Example:  `python src/main.py B07JZ7PW6F --from-browser --browser firefox`

Created/Tested on Windows 11 25H2 with Python 3.13.12. Not yet tested for other platforms.