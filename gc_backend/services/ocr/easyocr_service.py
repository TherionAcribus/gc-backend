"""Offline OCR engine using EasyOCR.

The EasyOCR reader is initialized once (singleton) to avoid expensive reloads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

from .image_utils import pil_image_to_rgb_array

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OcrResult:
    """Result of an OCR extraction."""

    text: str
    confidence: float
    lines: List[str]


def _preprocess_rgb_array(rgb_array, mode: str):
    """Apply optional preprocessing to a RGB array.

    Args:
        rgb_array: numpy ndarray (H, W, 3).
        mode: 'none' | 'threshold' | 'auto'

    Returns:
        numpy ndarray (H, W) or (H, W, 3)
    """
    if mode not in {"none", "threshold", "auto"}:
        mode = "auto"

    if mode == "none":
        return rgb_array

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return rgb_array

    img = rgb_array
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    if mode in {"threshold", "auto"}:
        gray = cv2.bilateralFilter(gray, 9, 75, 75)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        return thresh

    return gray


@lru_cache(maxsize=1)
def get_easyocr_reader():
    """Return the singleton EasyOCR reader.

    Raises:
        RuntimeError: if EasyOCR is not installed.
    """
    try:
        import easyocr  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"EasyOCR is not available: {exc}") from exc

    langs = ["fr", "en", "de", "es", "it", "nl", "pt"]
    reader = easyocr.Reader(langs, gpu=False)
    logger.info("EasyOCR initialized with languages %s", langs)
    return reader


def extract_text_from_image_bytes(image_bytes: bytes, language: str = "auto", preprocess: str = "auto") -> OcrResult:
    """Extract text using EasyOCR.

    Args:
        image_bytes: Raw image bytes.
        language: Currently informational only for EasyOCR (reader is multi-lang).
        preprocess: 'none' | 'threshold' | 'auto'

    Returns:
        OcrResult

    Raises:
        RuntimeError: for missing dependencies.
    """
    reader = get_easyocr_reader()

    rgb = pil_image_to_rgb_array(image_bytes)
    processed = _preprocess_rgb_array(rgb, preprocess)

    def _read_text(image_arr, *, paragraph: bool) -> tuple[List[str], List[float]]:
        results = reader.readtext(image_arr, detail=1, paragraph=paragraph)
        parsed_lines: List[str] = []
        parsed_confs: List[float] = []

        for item in results or []:
            try:
                _bbox, text, conf = item
            except Exception:
                continue
            text_str = (text or "").strip()
            if not text_str:
                continue
            parsed_lines.append(text_str)
            try:
                parsed_confs.append(float(conf))
            except Exception:
                pass

        return parsed_lines, parsed_confs

    lines, confs = _read_text(processed, paragraph=True)

    if not lines and preprocess != "none":
        logger.debug("EasyOCR fallback: retrying without preprocessing (paragraph=True)")
        lines, confs = _read_text(rgb, paragraph=True)

    if not lines:
        logger.debug("EasyOCR fallback: retrying with paragraph=False")
        lines, confs = _read_text(processed, paragraph=False)

    if not lines and preprocess != "none":
        logger.debug("EasyOCR fallback: retrying without preprocessing (paragraph=False)")
        lines, confs = _read_text(rgb, paragraph=False)

    full_text = "\n".join(lines).strip()
    confidence = sum(confs) / len(confs) if confs else (0.0 if not full_text else 0.75)

    return OcrResult(text=full_text, confidence=confidence, lines=lines)
