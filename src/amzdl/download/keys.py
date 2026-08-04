"""Content-key acquisition for the configured DRM device. Builds a license
challenge with the matching CDM — `pywidevine` from the manifest's TRACK_PSSH, or
`pyplayready` from a WRM header synthesised from the manifest's
`cenc:default_KID`, since Amazon publishes no PlayReady PSSH for any device type
— exchanges it through the signed `getLicenseForPlaybackV2` endpoint, and returns
the `kid:key` content key for the CENC decryptor. Both paths yield the same
AES-128-CTR key in the same format, so nothing downstream is aware of which DRM
produced it. One device is active per run and its refusal is fatal: a denied
license raises rather than retrying under the other CDM."""

import base64
import logging
import uuid

from amzdl.download.device import DrmDevice, DrmDeviceError
from amzdl.download.wvd import WVD

_log = logging.getLogger("downloader.keys")

_WRM_HEADER = (
    '<WRMHEADER xmlns="http://schemas.microsoft.com/DRM/2007/03/PlayReadyHeader" '
    'version="4.0.0.0"><DATA><PROTECTINFO><KEYLEN>16</KEYLEN><ALGID>AESCTR</ALGID>'
    "</PROTECTINFO><KID>{kid}</KID></DATA></WRMHEADER>"
)


def _wrm_header(kid_hex: str) -> str:
    kid = uuid.UUID(hex=kid_hex)
    return _WRM_HEADER.format(kid=base64.b64encode(kid.bytes_le).decode())


def _log_keys(keys, kid_of, key_of) -> None:
    for key in keys:
        _log.debug("key %s:%s", kid_of(key), key_of(key)[:8] + "...")


def _widevine_key(session, asin: str, representation: dict, device: DrmDevice) -> str:
    from pywidevine.cdm import Cdm
    from pywidevine.device import Device
    from pywidevine.pssh import PSSH

    pssh = representation.get("pssh")
    if not pssh:
        raise DrmDeviceError(f"no Widevine PSSH in the manifest for {asin}")

    loaded = Device.load(str(device.path)) if device.path else Device.loads(WVD)
    cdm = Cdm.from_device(loaded)
    session_id = cdm.open()
    try:
        challenge = cdm.get_license_challenge(session_id, PSSH(pssh))
        encoded = base64.b64encode(challenge).decode()
        _log.debug("widevine challenge (truncated): %s", encoded[:50])

        license_response = session.get_license_response(
            asin=asin, challenge=encoded, drm_type="WIDEVINE"
        )
        if not license_response:
            raise DrmDeviceError("the license server returned an empty response")

        cdm.parse_license(session_id, license_response)
        keys = cdm.get_keys(session_id)
        content_keys = [k for k in keys if k.type == "CONTENT"] or keys
        if not content_keys:
            raise DrmDeviceError(f"no content key returned for {asin}")

        _log_keys(content_keys, lambda k: k.kid.hex, lambda k: k.key.hex())
        chosen = content_keys[0]
        return f"{chosen.kid.hex}:{chosen.key.hex()}"
    finally:
        cdm.close(session_id)


def _playready_key(session, asin: str, representation: dict, device: DrmDevice) -> str:
    from pyplayready.cdm import Cdm
    from pyplayready.device import Device

    kid = representation.get("kid")
    if not kid:
        raise DrmDeviceError(f"no cenc:default_KID in the manifest for {asin}")

    cdm = Cdm.from_device(Device.load(str(device.path)))
    session_id = cdm.open()
    try:
        challenge = cdm.get_license_challenge(session_id, _wrm_header(kid))
        if isinstance(challenge, str):
            challenge = challenge.encode()
        encoded = base64.b64encode(challenge).decode()
        _log.debug("playready challenge (truncated): %s", encoded[:50])

        license_response = session.get_license_response(
            asin=asin, challenge=encoded, drm_type="PLAYREADY"
        )
        if not license_response:
            raise DrmDeviceError("the license server returned an empty response")

        soap = base64.b64decode(license_response).decode("utf-8", errors="ignore")
        cdm.parse_license(session_id, soap)
        keys = cdm.get_keys(session_id)
        if not keys:
            raise DrmDeviceError(f"no content key returned for {asin}")

        _log_keys(keys, lambda k: k.key_id.hex, lambda k: k.key.hex())
        wanted = uuid.UUID(hex=kid)
        chosen = next((k for k in keys if k.key_id == wanted), keys[0])
        return f"{chosen.key_id.hex}:{chosen.key.hex()}"
    finally:
        cdm.close(session_id)


class Keys:
    @staticmethod
    def getContentKeys(
        session, asin: str, representation: dict, device: DrmDevice
    ) -> str:
        if device.is_playready:
            return _playready_key(session, asin, representation, device)
        return _widevine_key(session, asin, representation, device)
