"""Plugin GeoApp: OCR offline via EasyOCR sur images de géocache.

Le plugin collecte les images (inputs.images ou images associées à la géocache) et renvoie un résultat
standardisé. L'OCR est effectué par le service EasyOCR réutilisable.
"""

from __future__ import annotations

import time
from io import BytesIO
from typing import Any, Dict, List, Optional

from loguru import logger

try:  # pragma: no cover - dépendances optionnelles
    import requests
except Exception as import_error:  # noqa: F401
    IMPORT_ERROR = import_error
    requests = None  # type: ignore
else:
    IMPORT_ERROR = None


class EasyOCROcrPlugin:
    """OCR offline via EasyOCR."""

    def __init__(self) -> None:
        self.name = "easyocr_ocr"
        self.version = "1.0.0"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()

        if IMPORT_ERROR is not None or requests is None:
            summary = f"Dépendances manquantes pour télécharger les images: {IMPORT_ERROR}"
            logger.error(summary)
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
        preprocess = str(inputs.get("preprocess") or "auto").strip().lower() or "auto"

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
            from gc_backend.services.ocr.easyocr_service import extract_text_from_image_bytes
        except Exception as exc:
            summary = f"Service EasyOCR indisponible: {exc}"
            logger.error(summary)
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
                ocr = extract_text_from_image_bytes(content, language=language, preprocess=preprocess)
            except Exception as exc:  # pragma: no cover
                logger.warning("[easyocr_ocr] OCR failed for {}: {}", full_url, exc)
                continue

            if not ocr.text.strip():
                logger.debug(
                    "[easyocr_ocr] No text detected for {} (confidence={})",
                    full_url,
                    ocr.confidence,
                )
                continue

            findings.append(
                {
                    "id": f"ocr_{len(findings) + 1}",
                    "text_output": ocr.text,
                    "confidence": ocr.confidence,
                    "image_url": full_url,
                    "method": "easyocr",
                    "metadata": {
                        "language": language,
                        "preprocess": preprocess,
                        "lines": ocr.lines,
                    },
                }
            )

        summary = (
            f"OCR EasyOCR: {len(findings)} résultat(s) sur {images_analyzed} image(s) analysée(s)"
            if images_analyzed
            else "Aucune image analysée"
        )

        if findings:
            total_chars = sum(len((f.get("text_output") or "")) for f in findings)
            logger.info(
                "[easyocr_ocr] Completed: {} findings, {} images analyzed, {} total chars",
                len(findings),
                images_analyzed,
                total_chars,
            )
        else:
            logger.info(
                "[easyocr_ocr] Completed: 0 findings, {} images analyzed",
                images_analyzed,
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
            "name": "easyocr_ocr",
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
            # Relative URL: assume geocaching.com
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
            logger.error("[easyocr_ocr] Cannot import Geocache: {}", exc)
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
                logger.warning("[easyocr_ocr] Failed parsing description_html: {}", exc)

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
            # Fast path: internal stored images -> read file directly if possible
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

            headers = {
                "User-Agent": "GeoApp/1.0 (+https://mysterai.io)",
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            }
            res = requests.get(url, timeout=20, headers=headers)
            if res.status_code != 200:
                logger.warning("[easyocr_ocr] Fetch failed for {} (HTTP {})", url, res.status_code)
                return None

            content_type = (res.headers.get("content-type") or "").split(";")[0].strip().lower()
            content = res.content or b""

            is_png = content.startswith(b"\x89PNG\r\n\x1a\n")
            is_jpeg = content.startswith(b"\xff\xd8")
            is_webp = content.startswith(b"RIFF") and len(content) > 12 and content[8:12] == b"WEBP"
            is_image = (content_type.startswith("image/")) or is_png or is_jpeg or is_webp

            if not is_image:
                preview = content[:200]
                try:
                    preview_text = preview.decode("utf-8", errors="replace")
                except Exception:
                    preview_text = str(preview)

                logger.warning(
                    "[easyocr_ocr] URL {} did not return an image (content-type={}). Preview: {}",
                    url,
                    content_type or "<missing>",
                    preview_text,
                )
                return None

            return content
        except Exception as exc:  # pragma: no cover
            logger.warning("[easyocr_ocr] Failed to fetch {}: {}", url, exc)
            return None


plugin = EasyOCROcrPlugin()
