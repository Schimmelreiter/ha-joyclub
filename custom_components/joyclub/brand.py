"""Own brand images from the configuration directory.

Home Assistant serves the brand images of a custom integration from its brand/ folder and reads
them on every request. Images placed in <config>/joyclub/brand/ are copied there at startup; without
any, the shipped neutral icon (default_brand/) is put back. An update of the integration replaces
brand/, the next start copies the own images again.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

IMAGES = {
    f"{dark}{kind}{size}.png" for dark in ("", "dark_") for kind in ("icon", "logo") for size in ("", "@2x")
}
_HERE = Path(__file__).parent


def apply_brand(own_dir: Path, brand_dir: Path = _HERE / "brand", default_dir: Path = _HERE / "default_brand") -> bool:
    """Make brand_dir hold the own images if there are any, else the defaults. True if own images are used."""
    own = {p.name: p for p in own_dir.glob("*.png") if p.name in IMAGES} if own_dir.is_dir() else {}
    source = own or {p.name: p for p in default_dir.glob("*.png") if p.name in IMAGES}
    if not source:
        return False
    brand_dir.mkdir(exist_ok=True)
    for name in IMAGES:
        target = brand_dir / name
        if name in source:
            if not target.is_file() or target.read_bytes() != source[name].read_bytes():
                shutil.copyfile(source[name], target)
        elif target.is_file():
            target.unlink()
    return bool(own)


async def async_apply_brand(hass: HomeAssistant) -> None:
    """apply_brand for <config>/joyclub/brand, in the executor; problems are only logged."""
    try:
        if await hass.async_add_executor_job(apply_brand, Path(hass.config.path(DOMAIN, "brand"))):
            _LOGGER.info("Using own brand images from %s", hass.config.path(DOMAIN, "brand"))
    except OSError as err:
        _LOGGER.warning("Cannot update the brand images: %s", err)
