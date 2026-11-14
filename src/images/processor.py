"""Image validation, cropping and resizing helpers.

Every sourced image passes through :func:`process_image_bytes`, which rejects
tiny or broken files, centre-crops to the configured aspect ratio, and resizes
to the target dimensions so the video stage gets a consistent input.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..utils import HttpClient

logger = logging.getLogger(__name__)


def parse_aspect(ratio: str) -> float:
    """Return ``width/height`` for an aspect string like ``"9:16"``.

    Falls back to 9:16 for malformed input.
    """
    try:
        w_str, h_str = str(ratio).split(":")
        w, h = float(w_str), float(h_str)
        if w <= 0 or h <= 0:
            return 9 / 16
        return w / h
    except (ValueError, AttributeError):
        return 9 / 16


def crop_to_aspect(image: Image.Image, aspect: float) -> Image.Image:
    """Centre-crop ``image`` to the given ``width/height`` aspect ratio."""
    w, h = image.size
    current = w / h
    if current > aspect:
        # Too wide — trim the sides.
        new_w = max(1, int(round(h * aspect)))
        left = (w - new_w) // 2
        box = (left, 0, left + new_w, h)
    else:
        # Too tall — trim top and bottom.
        new_h = max(1, int(round(w / aspect)))
        top = (h - new_h) // 2
        box = (0, top, w, top + new_h)
    return image.crop(box)


def process_image_bytes(
    data: bytes,
    *,
    min_width: int,
    min_height: int,
    aspect: float,
    target_size: tuple[int, int],
) -> Image.Image | None:
    """Decode, validate, crop and resize raw image bytes.

    Returns:
        A processed RGB :class:`PIL.Image.Image`, or ``None`` if the data is
        not a valid image or is smaller than the configured minimums.
    """
    try:
        image = Image.open(BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        logger.debug("Broken image skipped: %s", exc)
        return None

    if image.width < min_width or image.height < min_height:
        logger.debug("Image too small (%dx%d), skipped", image.width, image.height)
        return None

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    image = crop_to_aspect(image, aspect)
    image = image.resize(target_size, Image.Resampling.LANCZOS)
    return image


def save_jpeg(image: Image.Image, path: Path, quality: int = 88) -> Path:
    """Save an image as JPEG, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=quality, optimize=True)
    return path


def download_image(http: HttpClient, url: str) -> bytes | None:
    """Download raw image bytes through the robots-aware HTTP client."""
    resp = http.get(url)
    if resp is None:
        return None
    return resp.content
