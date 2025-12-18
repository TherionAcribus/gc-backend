"""Low-level utilities for downloading and storing geocache images on disk."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from pathlib import Path
from typing import Optional, Tuple

import requests


logger = logging.getLogger(__name__)


def get_images_root_dir() -> Path:
    """Return the root directory where geocache images are stored."""
    return Path(__file__).resolve().parents[2] / 'data' / 'geocache_images'


def ensure_geocache_dir(geocache_id: int) -> Path:
    """Ensure and return the directory used to store images for a geocache."""
    root = get_images_root_dir()
    root.mkdir(parents=True, exist_ok=True)
    geocache_dir = root / str(geocache_id)
    geocache_dir.mkdir(parents=True, exist_ok=True)
    return geocache_dir


def guess_extension(source_url: str, content_type: Optional[str]) -> str:
    """Guess a file extension based on content type and URL."""
    if content_type:
        content_type = content_type.split(';')[0].strip().lower()
        ext = mimetypes.guess_extension(content_type)
        if ext:
            return ext

    path = (source_url or '').split('?')[0]
    suffix = Path(path).suffix
    if suffix and len(suffix) <= 5:
        return suffix

    return '.jpg'


def download_image(source_url: str, timeout_sec: int = 20) -> Tuple[bytes, str, int]:
    """Download an image and return (content, content_type, status_code)."""
    res = requests.get(source_url, stream=True, timeout=timeout_sec)
    content_type = res.headers.get('content-type', '')
    return res.content, content_type, res.status_code


def write_image_file(geocache_id: int, image_id: int, content: bytes, content_type: Optional[str], source_url: str) -> Tuple[str, str, int, str]:
    """Write the image file to disk and return metadata.

    Returns:
        stored_path: str (relative path within the geocache_images root)
        mime_type: str
        byte_size: int
        sha256: str
    """
    geocache_dir = ensure_geocache_dir(geocache_id)

    ext = guess_extension(source_url, content_type)
    filename = f"{image_id}{ext}"
    full_path = geocache_dir / filename

    full_path.write_bytes(content)

    sha = hashlib.sha256(content).hexdigest()
    mime_type = (content_type or '').split(';')[0].strip() or mimetypes.guess_type(str(full_path))[0] or 'application/octet-stream'
    byte_size = len(content)

    stored_path = f"{geocache_id}/{filename}"
    return stored_path, mime_type, byte_size, sha


def remove_geocache_dir(geocache_id: int) -> None:
    """Remove all stored files for a geocache."""
    geocache_dir = get_images_root_dir() / str(geocache_id)
    if not geocache_dir.exists():
        return

    for path in geocache_dir.rglob('*'):
        if path.is_file():
            try:
                path.unlink()
            except Exception as exc:  # pragma: no cover
                logger.warning('Failed to delete file %s: %s', path, exc)

    try:
        for path in sorted(geocache_dir.glob('**/*'), reverse=True):
            if path.is_dir():
                path.rmdir()
        geocache_dir.rmdir()
    except Exception as exc:  # pragma: no cover
        logger.warning('Failed to delete directory %s: %s', geocache_dir, exc)
