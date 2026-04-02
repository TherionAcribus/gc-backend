"""Plugin MysterAI pour détecter et décoder les QR codes dans les images d'une géocache.

Ce plugin récupère une géocache depuis la base de données à partir de son ID,
collecte les URLs d'images associées (champ `images` + images dans `description_html`),
télécharge ces images et tente d'y détecter des QR codes.

La sortie est renvoyée au format standardisé attendu par le système de plugins.
"""

from __future__ import annotations

from typing import Dict, Any, List
from io import BytesIO
import time

from loguru import logger

# Imports optionnels pour la détection de QR codes
try:  # pragma: no cover - gestion d'environnement dynamique
    import requests
    from PIL import Image
    from pyzbar.pyzbar import decode as decode_qr
except Exception as import_error:  # noqa: F401
    IMPORT_ERROR = import_error
    requests = None  # type: ignore
    Image = None  # type: ignore
    decode_qr = None  # type: ignore
else:
    IMPORT_ERROR = None


class QRCodeDetectorPlugin:
    """Plugin pour détecter et décoder les QR codes dans les images d'une géocache."""

    def __init__(self) -> None:
        self.name = "qr_code_detector"
        self.version = "1.0.0"
        self.description = (
            "Détecte et décode les QR codes dans les images associées à une géocache"
        )

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin.

        Args:
            inputs: Dictionnaire contenant au minimum `geocache_id` (str ou int).

        Returns:
            Dictionnaire de résultat au format standardisé MysterAI.
        """
        start_time = time.time()

        # Vérification des dépendances
        if IMPORT_ERROR is not None or not (requests and Image and decode_qr):
            summary = (
                "Dépendances manquantes pour la détection de QR codes: "
                f"{IMPORT_ERROR}"
            )
            logger.error(summary)
            return {
                "status": "error",
                "summary": summary,
                "results": [],
                "qr_codes": [],
                "images_analyzed": 0,
                "plugin_info": self._build_plugin_info(start_time),
            }

        try:
            from gc_backend.blueprints.coordinates import detect_gps_coordinates  # type: ignore
        except Exception:  # pragma: no cover - dépend du contexte Flask
            detect_gps_coordinates = None  # type: ignore

        geocache_id_raw = inputs.get("geocache_id")
        explicit_images = inputs.get("images")
        has_explicit_images = isinstance(explicit_images, list) and any(
            isinstance((entry.get("url") if isinstance(entry, dict) else entry), str)
            and str(entry.get("url") if isinstance(entry, dict) else entry).strip()
            for entry in explicit_images
        )
        if geocache_id_raw is None and not has_explicit_images:
            summary = "Champ 'geocache_id' manquant dans les inputs"
            logger.warning(summary)
            return {
                "status": "error",
                "summary": summary,
                "results": [],
                "qr_codes": [],
                "images_analyzed": 0,
                "plugin_info": self._build_plugin_info(start_time),
            }

        geocache = None
        if geocache_id_raw is not None:
            # Import lazy pour éviter les problèmes de dépendances circulaires
            try:
                from gc_backend.database import db
                from gc_backend.geocaches.models import Geocache
            except Exception as exc:  # pragma: no cover - dépend du contexte Flask
                summary = f"Impossible d'importer les modèles Geocache: {exc}"
                logger.error(summary)
                return {
                    "status": "error",
                    "summary": summary,
                    "results": [],
                    "qr_codes": [],
                    "images_analyzed": 0,
                    "plugin_info": self._build_plugin_info(start_time),
                }

            geocache_id_str = str(geocache_id_raw).strip()

            # 1) Tentative par ID numérique (clé primaire)
            try:
                geocache_id_int = int(geocache_id_str)
            except (TypeError, ValueError):  # Non numérique -> on ignore cette étape
                geocache_id_int = None

            if geocache_id_int is not None:
                geocache = db.session.query(Geocache).get(geocache_id_int)

            # 2) Si introuvable ou non numérique, tentative par code GC (gc_code)
            if geocache is None:
                geocache = (
                    db.session.query(Geocache)
                    .filter(Geocache.gc_code == geocache_id_str)
                    .first()
                )

            if not geocache and not has_explicit_images:
                summary = f"Géocache introuvable pour identifiant {geocache_id_str!r}"
                logger.warning(summary)
                return {
                    "status": "error",
                    "summary": summary,
                    "results": [],
                    "qr_codes": [],
                    "images_analyzed": 0,
                    "plugin_info": self._build_plugin_info(start_time),
                }

        image_urls: List[str] = []
        if isinstance(explicit_images, list):
            for entry in explicit_images:
                url = None
                if isinstance(entry, dict):
                    url = entry.get("url")
                else:
                    url = entry
                if isinstance(url, str) and url.strip():
                    image_urls.append(url.strip())

        if not image_urls and geocache is not None:
            image_urls = self._collect_image_urls(geocache)

        logger.info(
            "[qr_code_detector] %d URL(s) d'images collectées pour %s",
            len(image_urls),
            geocache.gc_code if geocache is not None else "les images explicites",
        )

        if not image_urls:
            summary = "Aucune image associée à cette géocache"
            return {
                "status": "success",
                "summary": summary,
                "results": [],
                "qr_codes": [],
                "images_analyzed": 0,
                "plugin_info": self._build_plugin_info(start_time),
            }

        findings: List[Dict[str, Any]] = []
        qr_codes: List[Dict[str, Any]] = []
        images_analyzed = 0
        primary_coordinates = None

        for url in image_urls:
            full_url = self._normalize_image_url(url)
            try:
                response = requests.get(full_url, timeout=10)
                if response.status_code != 200:
                    logger.warning(
                        "[qr_code_detector] Impossible de télécharger %s (status=%s)",
                        full_url,
                        response.status_code,
                    )
                    continue

                images_analyzed += 1
                image = Image.open(BytesIO(response.content))
                image = image.convert("RGB")
                decoded_items = decode_qr(image)
            except Exception as exc:  # pragma: no cover - dépend du contenu distant
                logger.warning(
                    "[qr_code_detector] Erreur lors du traitement de %s: %s",
                    full_url,
                    exc,
                )
                continue

            for decoded in decoded_items:
                data_bytes = decoded.data or b""
                text = data_bytes.decode("utf-8", errors="replace")
                barcode_type = str(getattr(decoded, "type", "") or "QRCODE").strip().upper() or "QRCODE"

                rect = getattr(decoded, "rect", None)
                rect_dict = {
                    "left": getattr(rect, "left", None) if rect is not None else None,
                    "top": getattr(rect, "top", None) if rect is not None else None,
                    "width": getattr(rect, "width", None) if rect is not None else None,
                    "height": getattr(rect, "height", None) if rect is not None else None,
                }

                polygon_points = []
                polygon = getattr(decoded, "polygon", None)
                if polygon:
                    for point in polygon:
                        if hasattr(point, "x") and hasattr(point, "y"):
                            polygon_points.append({"x": point.x, "y": point.y})

                qr_index = len(qr_codes) + 1

                coordinates_info = None
                decimal_lat = None
                decimal_lon = None

                if detect_gps_coordinates and text:
                    try:
                        detection = detect_gps_coordinates(text)
                    except Exception as exc:  # pragma: no cover - dépend du contenu
                        logger.warning(
                            "[qr_code_detector] Erreur detect_gps_coordinates pour %s: %s",
                            full_url,
                            exc,
                        )
                        detection = None

                    if detection and detection.get("exist"):
                        decimal_lat = detection.get("decimal_latitude")
                        decimal_lon = detection.get("decimal_longitude")
                        ddm_lat = detection.get("ddm_lat")
                        ddm_lon = detection.get("ddm_lon")
                        ddm = detection.get("ddm")

                        formatted = ddm
                        if not formatted and ddm_lat and ddm_lon:
                            formatted = f"{ddm_lat} {ddm_lon}"

                        coordinates_info = {
                            "latitude": ddm_lat or "",
                            "longitude": ddm_lon or "",
                            "formatted": formatted or "",
                            "decimalLatitude": decimal_lat,
                            "decimalLongitude": decimal_lon,
                            "decimal_latitude": decimal_lat,
                            "decimal_longitude": decimal_lon,
                        }

                        if (
                            primary_coordinates is None
                            and decimal_lat is not None
                            and decimal_lon is not None
                        ):
                            primary_coordinates = {
                                "latitude": decimal_lat,
                                "longitude": decimal_lon,
                            }

                findings.append(
                    {
                        "id": f"qr_{qr_index}",
                        "text_output": (
                            f"{barcode_type} détecté dans une image: {text[:80]}"
                        ),
                        "qr_data": text,
                        "barcode_type": barcode_type,
                        "image_url": full_url,
                        "confidence": 1.0,
                        "coordinates": coordinates_info,
                        "decimal_latitude": decimal_lat,
                        "decimal_longitude": decimal_lon,
                    }
                )

                qr_codes.append(
                    {
                        "data": text,
                        "type": barcode_type,
                        "image_url": full_url,
                        "rect": rect_dict,
                        "polygon": polygon_points,
                    }
                )

                logger.info(
                    "[qr_code_detector] %s trouvé dans %s: %s...",
                    barcode_type,
                    full_url,
                    text[:80],
                )

        if not qr_codes:
            summary = (
                f"Aucun QR code / code-barres détecté dans {images_analyzed} image(s) analysée(s)"
            )
        else:
            summary = (
                f"{len(qr_codes)} code(s) QR / barcode détecté(s) dans "
                f"{images_analyzed} image(s) analysée(s)"
            )

        return {
            "status": "success",
            "summary": summary,
            "results": findings,
            "qr_codes": qr_codes,
            "images_analyzed": images_analyzed,
            "primary_coordinates": primary_coordinates,
            "plugin_info": self._build_plugin_info(start_time),
        }

    def _collect_image_urls(self, geocache: Any) -> List[str]:
        """Collecte et déduplique les URLs d'images associées à une géocache.

        - Utilise en priorité le champ `images` (liste d'objets {url: str}).
        - Complète avec les balises <img> trouvées dans `description_html`.
        """
        urls: List[str] = []

        # 1) Images stockées dans le champ JSON `images`
        images_field = geocache.images or []
        if isinstance(images_field, list):
            for entry in images_field:
                if isinstance(entry, dict):
                    url = entry.get("url")
                else:
                    url = None
                if isinstance(url, str) and url.strip():
                    urls.append(url.strip())

        # 2) Images présentes dans la description HTML (fallback)
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
                logger.warning(
                    "[qr_code_detector] Erreur lors de l'analyse de description_html: %s",
                    exc,
                )

        # Déduplication en préservant l'ordre
        seen = set()
        unique_urls: List[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        return unique_urls

    @staticmethod
    def _normalize_image_url(url: str) -> str:
        """Normalise une URL d'image potentiellement relative.

        - "//..." -> "https://..."
        - URL relative -> préfixée par "https://www.geocaching.com"
        """
        url = (url or "").strip()
        if url.startswith("//"):
            return "https:" + url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return "https://www.geocaching.com" + url

    def _build_plugin_info(self, start_time: float) -> Dict[str, Any]:
        """Construit la section `plugin_info` du résultat."""
        duration_ms = int((time.time() - start_time) * 1000)
        return {
            "name": self.name,
            "version": self.version,
            "execution_time_ms": duration_ms,
        }


plugin = QRCodeDetectorPlugin()


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Fonction de compatibilité pour le chargement dynamique du plugin."""
    return plugin.execute(inputs)

