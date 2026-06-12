"""Widevine content-key acquisition.

Builds a `pywidevine` license challenge from a provisioned Widevine device file,
exchanges it through the signed `getLicenseForPlaybackV2` endpoint
(`AmazonMusicMobileAPI.get_license_response()`), and returns the decrypted content
key as a `kid:key` string for `mp4decrypt`.
"""

import base64
import logging

from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.pssh import PSSH

_log = logging.getLogger("downloader.keys")


class Keys:
    @staticmethod
    def getContentKeys(session, asin: str, psshStr: str, wvd_path: str = "device.wvd") -> str:
        # PSSH usually lives in the MPD ContentProtection (web/TRACK_PSSH here).
        pssh = PSSH(psshStr)

        # Load the provisioned Widevine device + CDM.
        device = Device.load(wvd_path)
        cdm = Cdm.from_device(device)

        session_id = cdm.open()
        try:
            challenge = cdm.get_license_challenge(session_id, pssh)
            base64_challenge = base64.b64encode(challenge).decode("utf-8")
            _log.debug("license challenge (truncated): %s", base64_challenge[:50])

            # Signed license request via the Amazon Music API.
            license_response = session.get_license_response(
                asin=asin, challenge=base64_challenge, drm_type="WIDEVINE"
            )
            if not license_response:
                raise ValueError("Failed to communicate with the license server")
            _log.debug("received license (truncated): %s", license_response[:50])

            cdm.parse_license(session_id, license_response)
            keys = cdm.get_keys(session_id)

            content_keys = [k for k in keys if k.type == "CONTENT"] or keys
            for key in content_keys:
                _log.debug("key [%s] %s:%s", key.type, key.kid.hex, key.key.hex())

            first_key = content_keys[0]
            return f"{first_key.kid.hex}:{first_key.key.hex()}"
        finally:
            cdm.close(session_id)
