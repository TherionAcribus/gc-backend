import time
from typing import Any, Dict, List, Optional

from loguru import logger


class WrittenCoordsConverterPlugin:
    def __init__(self):
        self.name = "written_coords_converter"
        self.description = "Reconnaissance de coordonnées GPS écrites en toutes lettres (orchestrateur)"
        self._plugin_manager = None

        self._auto_fr_markers = {
            "nord",
            "sud",
            "est",
            "ouest",
            "degre",
            "degres",
            "virgule",
        }
        self._auto_en_markers = {
            "north",
            "south",
            "east",
            "west",
            "degree",
            "degrees",
            "comma",
        }

    def set_plugin_manager(self, plugin_manager):
        self._plugin_manager = plugin_manager

    def _get_plugin_manager(self):
        if self._plugin_manager is not None:
            return self._plugin_manager
        try:
            from gc_backend.blueprints.plugins import get_plugin_manager

            return get_plugin_manager()
        except Exception:
            return None

    def _parse_languages(self, raw: Any) -> List[str]:
        if raw is None:
            return ["fr"]
        if isinstance(raw, list):
            langs = [str(x).strip().lower() for x in raw if str(x).strip()]
            return langs or ["fr"]
        raw_s = str(raw)
        parts = [p.strip().lower() for p in raw_s.split(",")]
        parts = [p for p in parts if p]
        return parts or ["fr"]

    def _guess_languages(self, text: str) -> List[str]:
        s = (text or "").lower()
        hits_fr = any(m in s for m in self._auto_fr_markers)
        hits_en = any(m in s for m in self._auto_en_markers)
        if hits_fr and hits_en:
            return ["fr", "en"]
        if hits_en:
            return ["en"]
        if hits_fr:
            return ["fr"]
        return ["fr"]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()

        text = inputs.get("text", "")
        languages = self._parse_languages(inputs.get("languages"))
        if any(l in {"auto", "*"} for l in languages):
            languages = self._guess_languages(text)
        max_candidates_raw = inputs.get("max_candidates", 20)
        include_deconcat = bool(inputs.get("include_deconcat", True))
        origin_coords = inputs.get("origin_coords")

        try:
            max_candidates = int(max_candidates_raw)
        except Exception:
            max_candidates = 20

        if not text:
            return {
                "status": "success",
                "summary": "Aucun texte fourni",
                "results": [],
                "primary_coordinates": None,
                "combined_results": {},
                "plugin_info": {
                    "name": self.name,
                    "version": "1.0.0",
                    "execution_time_ms": int((time.time() - start) * 1000),
                },
            }

        plugin_manager = self._get_plugin_manager()
        if not plugin_manager:
            return {
                "status": "error",
                "summary": "PluginManager non disponible",
                "results": [],
                "primary_coordinates": None,
                "combined_results": {},
                "plugin_info": {
                    "name": self.name,
                    "version": "1.0.0",
                    "execution_time_ms": int((time.time() - start) * 1000),
                },
            }

        try:
            from gc_backend.blueprints.coordinates import detect_gps_coordinates
        except Exception:
            detect_gps_coordinates = None

        combined_results: Dict[str, Any] = {}
        all_candidates: List[Dict[str, Any]] = []

        for lang in languages:
            child_name = f"written_coords_{lang}".strip()
            child_inputs = {
                "text": text,
                "language": lang,
                "max_candidates": max_candidates,
                "include_deconcat": include_deconcat,
            }

            if origin_coords is not None:
                child_inputs["origin_coords"] = origin_coords

            try:
                child_result = plugin_manager.execute_plugin(child_name, child_inputs)
            except Exception as e:
                logger.warning("Erreur exécution sous-plugin %s: %s", child_name, e)
                child_result = {"status": "error", "summary": str(e), "results": []}

            combined_results[child_name] = child_result

            for item in (child_result or {}).get("results", []) or []:
                if not isinstance(item, dict):
                    continue
                cand_text = (item.get("text_output") or "").strip()
                if not cand_text:
                    continue

                child_conf = item.get("confidence", 0.5)
                validated = None
                if detect_gps_coordinates is not None:
                    try:
                        validated = detect_gps_coordinates(cand_text)
                    except Exception:
                        validated = None

                exist = False
                if isinstance(validated, dict):
                    exist = bool(
                        validated.get("exist")
                        or (validated.get("ddm_lat") and validated.get("ddm_lon"))
                        or validated.get("ddm")
                    )
                final_conf = float(child_conf or 0.0)
                coords_obj = None
                if exist:
                    coords_obj = validated
                    try:
                        final_conf = float((validated or {}).get("confidence", 0.8))
                    except Exception:
                        final_conf = float(child_conf or 0.0)

                all_candidates.append(
                    {
                        "id": item.get("id") or f"{child_name}_{len(all_candidates) + 1}",
                        "text_output": cand_text,
                        "confidence": final_conf,
                        "coordinates": coords_obj,
                        "decimal_latitude": (coords_obj or {}).get("decimal_latitude"),
                        "decimal_longitude": (coords_obj or {}).get("decimal_longitude"),
                        "metadata": {
                            "source": "written_coords",
                            "language": lang,
                            "child_plugin": child_name,
                            "child_confidence": child_conf,
                            "validated": exist,
                            "child_metadata": item.get("metadata") or {},
                        },
                    }
                )

        all_candidates.sort(key=lambda x: float(x.get("confidence") or 0.0), reverse=True)
        all_candidates = all_candidates[:max_candidates]

        primary_coordinates = None
        for cand in all_candidates:
            coords = cand.get("coordinates")
            if isinstance(coords, dict) and coords.get("exist"):
                primary_coordinates = coords
                break

        if primary_coordinates is None and detect_gps_coordinates is not None:
            for cand in all_candidates:
                cand_text = (cand.get("text_output") or "").strip()
                if not cand_text:
                    continue
                try:
                    validated = detect_gps_coordinates(cand_text)
                except Exception:
                    validated = None

                if isinstance(validated, dict) and validated.get("exist"):
                    cand["coordinates"] = validated
                    cand["metadata"]["validated"] = True
                    cand["decimal_latitude"] = validated.get("decimal_latitude")
                    cand["decimal_longitude"] = validated.get("decimal_longitude")
                    try:
                        cand["confidence"] = float(validated.get("confidence", cand.get("confidence") or 0.0))
                    except Exception:
                        pass
                    primary_coordinates = validated
                    break

        summary = f"{len(all_candidates)} candidats, {1 if primary_coordinates else 0} coordonnée validée"

        return {
            "status": "success",
            "summary": summary,
            "results": all_candidates,
            "primary_coordinates": primary_coordinates,
            "combined_results": combined_results,
            "plugin_info": {
                "name": self.name,
                "version": "1.0.0",
                "execution_time_ms": int((time.time() - start) * 1000),
            },
        }


plugin = WrittenCoordsConverterPlugin()


def execute(inputs):
    return plugin.execute(inputs)
