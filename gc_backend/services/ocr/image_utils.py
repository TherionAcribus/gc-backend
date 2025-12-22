"""Image helper utilities for OCR services.

This module contains small helpers for decoding and identifying images.
"""

from __future__ import annotations

import base64
from io import BytesIO
from typing import Tuple


def detect_mime_type(image_bytes: bytes) -> str:
    """Detect a best-effort mime type from image bytes.

    Args:
        image_bytes: Raw image bytes.

    Returns:
        Mime type string.
    """
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and len(image_bytes) > 12 and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def to_data_url(image_bytes: bytes) -> Tuple[str, str]:
    """Convert image bytes to a data URL.

    Args:
        image_bytes: Raw image bytes.

    Returns:
        Tuple of (data_url, mime_type).
    """
    mime_type = detect_mime_type(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}", mime_type


def pil_image_to_rgb_array(image_bytes: bytes):
    """Decode bytes into a RGB numpy array using Pillow.

    Args:
        image_bytes: Raw image bytes.

    Returns:
        numpy ndarray (H, W, 3).

    Raises:
        RuntimeError: If Pillow or numpy is not available.
        ValueError: If the image cannot be decoded.
    """
    try:
        from PIL import Image  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Missing Pillow/numpy dependencies: {exc}") from exc

    with Image.open(BytesIO(image_bytes)) as img:
        img = img.convert("RGB")
        return np.array(img)
