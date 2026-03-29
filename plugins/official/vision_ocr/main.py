"""Plugin GeoApp: OCR IA (vision) via endpoint OpenAI-compatible (LMStudio).

Ce plugin envoie l'image au modèle vision via une requête HTTP (base64 data-url).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

try:  # pragma: no cover
    import requests
except Exception as import_error:  # noqa: F401
    IMPORT_ERROR = import_error
    requests = None  # type: ignore
else:
    IMPORT_ERROR = None


class VisionOCRPlugin:
    """OCR via modèle vision (LMStudio actuellement)."""

    def __init__(self) -> None:
        self.name = "vision_ocr"
        self.version = "1.0.0"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()

        if IMPORT_ERROR is not None or requests is None:
            summary = f"Dépendances manquantes: {IMPORT_ERROR}"
            return {
                "status": "error",
                "summary": summary,
                "results": [],
                "images_analyzed": 0,
                "plugin_info": self._build_plugin_info(start),
            }

        geocache_id_raw = inputs.get("geocache_id")
        explicit_images = self._collect_explicit_images(inputs)
        if geocache_id_raw is None and not explicit_images:
            summary = "Champ 'geocache_id' manquant dans les inputs"
            return {
                "status": "error",
                "summary": summary,
                "results": [],
                "images_analyzed": 0,
                "plugin_info": self._build_plugin_info(start),
            }

        language = str(inputs.get("language") or "auto").strip().lower() or "auto"

        from gc_backend.utils.preferences import get_value_or_default

        base_url = str(inputs.get("base_url") or get_value_or_default("geoApp.ocr.lmstudio.baseUrl", "http://localhost:1234"))
        model = str(inputs.get("model") or get_value_or_default("geoApp.ocr.lmstudio.model", ""))

        geocache = None
        if geocache_id_raw is not None:
            geocache_id_str = str(geocache_id_raw).strip()
            geocache = self._load_geocache(geocache_id_str)
            if not geocache and not explicit_images:
                summary = f"Géocache introuvable pour identifiant {geocache_id_str!r}"
                return {
                    "status": "error",
                    "summary": summary,
                    "results": [],
                    "images_analyzed": 0,
                    "plugin_info": self._build_plugin_info(start),
                }

        image_urls = explicit_images or (self._collect_image_urls(geocache) if geocache else [])
        if not image_urls:
            return {
                "status": "success",
                "summary": "Aucune image associée à cette géocache",
                "results": [],
                "images_analyzed": 0,
                "plugin_info": self._build_plugin_info(start),
            }

        try:
            from gc_backend.services.ocr.lmstudio_vision_service import (
                DEFAULT_STRICT_PROMPT,
                vision_ocr_via_lmstudio,
            )
        except Exception as exc:
            summary = f"Service Vision OCR indisponible: {exc}"
            return {
                "status": "error",
                "summary": summary,
                "results": [],
                "images_analyzed": 0,
                "plugin_info": self._build_plugin_info(start),
            }

        findings: List[Dict[str, Any]] = []
        images_analyzed = 0

        for url in image_urls:
            full_url = self._normalize_image_url(url)
            content = self._fetch_image_bytes(full_url)
            if not content:
                continue
            images_analyzed += 1

            try:
                ocr = vision_ocr_via_lmstudio(
                    image_bytes=content,
                    base_url=base_url,
                    model=model,
                    prompt=DEFAULT_STRICT_PROMPT,
                    timeout_sec=90,
                )
            except Exception as exc:  # pragma: no cover
                logger.warning("[vision_ocr] Vision OCR failed for {}: {}", full_url, exc)
                continue

            text = (ocr.text or "").strip()
            try:
                from gc_backend.services.ocr.lmstudio_vision_service import strip_thinking_blocks

                text = strip_thinking_blocks(text)
            except Exception:
                pass

            if not text.strip():
                continue

            findings.append(
                {
                    "id": f"vision_ocr_{len(findings) + 1}",
                    "text_output": text,
                    "confidence": 0.95,
                    "image_url": full_url,
                    "method": "lmstudio",
                    "metadata": {
                        "provider": ocr.provider,
                        "model": ocr.model,
                        "language": language,
                    },
                }
            )

        summary = (
            f"Vision OCR: {len(findings)} résultat(s) sur {images_analyzed} image(s) analysée(s)"
            if images_analyzed
            else "Aucune image analysée"
        )

        return {
            "status": "success",
            "summary": summary,
            "results": findings,
            "images_analyzed": images_analyzed,
            "plugin_info": self._build_plugin_info(start),
        }

    @staticmethod
    def _build_plugin_info(start_time: float) -> Dict[str, Any]:
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "name": "vision_ocr",
            "version": "1.0.0",
            "execution_time_ms": duration_ms,
        }

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        url = (url or "").strip()
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if url.startswith("/"):
            return "https://www.geocaching.com" + url
        return url

    @staticmethod
    def _collect_explicit_images(inputs: Dict[str, Any]) -> List[str]:
        image_urls: List[str] = []
        explicit_images = inputs.get("images")
        if isinstance(explicit_images, list):
            for entry in explicit_images:
                url = None
                if isinstance(entry, dict):
                    url = entry.get("url")
                else:
                    url = entry
                if isinstance(url, str) and url.strip():
                    image_urls.append(url.strip())
        return image_urls

    @staticmethod
    def _load_geocache(geocache_id_str: str) -> Optional[Any]:
        try:
            from gc_backend.database import db
            from gc_backend.geocaches.models import Geocache
        except Exception as exc:  # pragma: no cover
            logger.error("[vision_ocr] Cannot import Geocache: %s", exc)
            return None

        geocache = None
        try:
            geocache_id_int = int(geocache_id_str)
        except (TypeError, ValueError):
            geocache_id_int = None

        if geocache_id_int is not None:
            geocache = db.session.query(Geocache).get(geocache_id_int)

        if geocache is None:
            geocache = (
                db.session.query(Geocache)
                .filter(Geocache.gc_code == geocache_id_str)
                .first()
            )

        return geocache

    @staticmethod
    def _collect_image_urls(geocache: Any) -> List[str]:
        urls: List[str] = []

        images_field = geocache.images or []
        if isinstance(images_field, list):
            for entry in images_field:
                url = entry.get("url") if isinstance(entry, dict) else None
                if isinstance(url, str) and url.strip():
                    urls.append(url.strip())

        description_html = getattr(geocache, "description_html", None)
        if description_html:
            try:
                from bs4 import BeautifulSoup

                soup = BeautifulSoup(description_html, "html.parser")
                for img in soup.find_all("img"):
                    src = img.get("src")
                    if isinstance(src, str) and src.strip():
                        urls.append(src.strip())
            except Exception as exc:
                logger.warning("[vision_ocr] Failed parsing description_html: %s", exc)

        seen = set()
        unique: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                unique.append(u)
        return unique

    @staticmethod
    def _fetch_image_bytes(url: str) -> Optional[bytes]:
        try:
            if "/api/geocache-images/" in url and url.rstrip("/").endswith("/content"):
                try:
                    image_id_str = url.split("/api/geocache-images/")[1].split("/")[0]
                    image_id = int(image_id_str)
                except Exception:
                    image_id = None
                if image_id is not None:
                    try:
                        from gc_backend.geocaches.models import GeocacheImage

                        img = GeocacheImage.query.get(image_id)
                        if img and img.stored and img.stored_path:
                            from gc_backend.blueprints.geocache_images import _safe_resolve_stored_file

                            file_path = _safe_resolve_stored_file(img.stored_path)
                            return file_path.read_bytes()
                    except Exception:
                        pass

            res = requests.get(url, timeout=30)
            if res.status_code != 200:
                return None
            return res.content
        except Exception as exc:  # pragma: no cover
            logger.warning("[vision_ocr] Failed to fetch {}: {}", url, exc)
            return None


plugin = VisionOCRPlugin()
