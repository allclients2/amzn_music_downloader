from pathlib import Path;
from mpd_selector import MPDStreamSelector;

raw_xml = Path("C:\\Users\\brend\\Downloads\\decryptstuff\\src\\mpd.xml").read_text();

selector = MPDStreamSelector(raw_xml);

result = selector.select();

if result:
    print("selected Base URL:");
    print(result["base_url"]);
    print("\nassociated PSSH:");
    print(result["pssh"]);
else:
    print("no selection made.");