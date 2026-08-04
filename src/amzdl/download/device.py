"""The single DRM device a run is configured with. `DrmDevice` pairs the device
file's path with its type so every stage downstream knows which CDM to build
without re-inspecting the path, and the type is derived once from the file
suffix: `.wvd` for Widevine, `.prd` for PlayReady. Exactly one device is active
per run; with none configured the embedded Widevine device is used. A configured
device that is missing or unrecognised raises, and a device whose license request
is refused is fatal — nothing here falls back to another CDM."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

WIDEVINE = "wvd"
PLAYREADY = "prd"

_SUFFIX_TYPES = {".wvd": WIDEVINE, ".prd": PLAYREADY}
_TYPE_NAMES = {WIDEVINE: "Widevine", PLAYREADY: "PlayReady"}


class DrmDeviceError(Exception):
    pass


@dataclass(frozen=True)
class DrmDevice:
    path: Path | None
    type: Literal["wvd", "prd"]

    @property
    def is_playready(self) -> bool:
        return self.type == PLAYREADY

    @property
    def name(self) -> str:
        return _TYPE_NAMES[self.type]

    def __str__(self) -> str:
        return f"{self.name} ({self.path.name if self.path else 'built-in'})"


EMBEDDED_WIDEVINE = DrmDevice(path=None, type=WIDEVINE)


def resolve_device(path: str | Path | None) -> DrmDevice:
    if not path:
        return EMBEDDED_WIDEVINE

    resolved = Path(path).expanduser()
    device_type = _SUFFIX_TYPES.get(resolved.suffix.lower())
    if device_type is None:
        raise DrmDeviceError(
            f"Unrecognised DRM device '{resolved.name}': expected a .wvd "
            "(Widevine) or .prd (PlayReady) file."
        )
    if not resolved.is_file():
        raise DrmDeviceError(f"{_TYPE_NAMES[device_type]} device not found: {resolved}")
    return DrmDevice(path=resolved, type=device_type)
