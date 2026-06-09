"""Widevine content-key acquisition.

The license exchange now goes through the signed `getLicenseForPlaybackV2`
endpoint via the vendored `AmazonMusicMobileAPI.get_license_response()` (RSA
signed), replacing the old web-player `accessToken`/CSRF request. The pywidevine
challenge/parse/keys flow is unchanged and still uses `device.wvd`.
"""

import base64

from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH


class Keys:
    @staticmethod
    def getContentKeys(session, asin: str, psshStr: str) -> str:
        # PSSH usually lives in the MPD ContentProtection (web/TRACK_PSSH here).
        pssh = PSSH(psshStr)

        # Load the provisioned Widevine device + CDM.
        device = Device.load("device.wvd")
        cdm = Cdm.from_device(device)

        session_id = cdm.open()
        try:
            challenge = cdm.get_license_challenge(session_id, pssh)
            base64_challenge = base64.b64encode(challenge).decode("utf-8")
            print("license challenge (truncated):", base64_challenge[:50])

            # Signed license request via the vendored Amazon Music API.
            license_response = session.get_license_response(
                asin=asin, challenge=base64_challenge, drm_type="WIDEVINE"
            )
            if not license_response:
                raise ValueError("Failed to communicate with the license server")
            print("received license (truncated):", license_response[:50])

            cdm.parse_license(session_id, license_response)
            keys = cdm.get_keys(session_id)

            content_keys = [k for k in keys if k.type == "CONTENT"] or keys
            print("received keys:")
            for key in content_keys:
                print(f"\t[{key.type}] {key.kid.hex}:{key.key.hex()}")

            first_key = content_keys[0]
            return f"{first_key.kid.hex}:{first_key.key.hex()}"
        finally:
            cdm.close(session_id)
