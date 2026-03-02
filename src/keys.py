from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH
import requests
import base64

class Keys:
    @staticmethod
    def getLicense(licenseChallenge: str, config, cookieHeader: str) -> str:
        # com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getLicenseForPlaybackV2

        url = "https://music.amazon.co.jp/FE/api/dmls/"

        headers = {
            "Authorization": "Bearer " + config["accessToken"],
            "Cookie": cookieHeader,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
            "X-Amz-Target": "com.amazon.digitalmusiclocator.DigitalMusicLocatorServiceExternal.getLicenseForPlaybackV2",
            "Csrf-Token": config["csrf"]["token"],
            "Csrf-Rnd": config["csrf"]["rnd"],
            "Csrf-Ts": config["csrf"]["ts"],
            "Content-Encoding": "amz-1.0",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://music.amazon.co.jp"
        }

        payload = {
            "DrmType": "WIDEVINE",
            # licenseChallenge provided by api which is from the Content Decryption Module (CDM) implementation
            "licenseChallenge": licenseChallenge,
            "customerId": config["customerId"],
            "deviceToken": {
                "deviceTypeId": config["deviceType"],
                "deviceId": config["deviceId"]
            },
            "appInfo": {
                "musicAgent": "Maestro/1.0 WebCP/1.0.9527.0 (d6c2-9680-WebC-0d59-617be)"
            },
            "Authorization": "Bearer " + config["accessToken"]
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload
        )


        print("Status Code:", response.status_code)
        print("Response Body:", response.text)

        # Convert JSON response into a Python dictionary
        data = response.json()

        # Get the value of the "license" field
        license_value: str = data["license"] 

        return license_value
    
    @staticmethod
    def getContentKeys(psshStr: str, config, cookieHeader):
        # prepare pssh (usually inside the MPD/M3U8, an API response, the player page, or inside the pssh mp4 box)
        pssh = PSSH(psshStr)

        # load device from a WVD file (your provision)
        device = Device.load("C:\\Users\\brend\\Downloads\\decryptstuff\\device.wvd")
        
        # load cdm (creating a CDM instance using that device)
        cdm = Cdm.from_device(device)

        # open cdm session (note that any one device should have a practical limit to amount of sessions open at any one time)
        session_id = cdm.open()

        # get license challenge (generate a license request message, signed using the device with the pssh)
        challenge = cdm.get_license_challenge(session_id, pssh)

        base64_challenge = base64.b64encode(challenge).decode("utf-8")

        print("license challenge base64:", base64_challenge)

        # send license challenge to bitmovin's license server (which has no auth and asks simply for the license challenge as-is)
        # another license server may require authentication and ask for it as JSON or form data instead
        # you may also be required to use privacy mode, where you use their service certificate when creating the challenge
        license = Keys.getLicense(base64_challenge, config, cookieHeader)

        print("license2:", license)

        # parse the license response message received from the license server API
        cdm.parse_license(session_id, license)

        keys = cdm.get_keys(session_id)

        # print keys
        for key in keys:
            print(f"[{key.type}] {key.kid.hex}:{key.key.hex()}")

        # finished, close the session, disposing of all keys and other related data
        cdm.close(session_id)

        first_key = keys[0]
        return f"{first_key.kid.hex}:{first_key.key.hex()}"

