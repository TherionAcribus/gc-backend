"""Plugin metasolver pour orchestrer l'exécution de plusieurs plugins MysterAI.

Ce plugin agit comme un "meta-plugin" : il peut lancer en séquence un ensemble de
plugins d'analyse (mode "detect") ou de décodage (mode "decode") et agréger leurs
résultats.

Le comportement est configurable via les paramètres d'entrée définis dans
``plugin.json`` afin d'adapter la portée (plugins considérés), les options de
bruteforce ou encore la détection automatique de coordonnées.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


# Liste temporaire de plugins à utiliser lorsque la découverte ne renvoie rien.
# TODO: remplacer par une récupération dynamique depuis le backend (configurable).
FALLBACK_PLUGIN_PIPELINE: Dict[str, List[str]] = {
    "decode": ["caesar", "bacon_code", "fox_code"],
    "detect": ["caesar", "bacon_code", "fox_code"],
}


def _lazy_import_wrappers():
    """Importe les wrappers de plugin seulement si nécessaire."""

    from gc_backend.plugins.wrappers import PluginMetadata, create_plugin_wrapper  # type: ignore

    return PluginMetadata, create_plugin_wrapper


class MetaSolverPlugin:
    """Plugin orchestrateur pour les autres plugins MysterAI."""

    def __init__(self) -> None:
        self.name = "metasolver"
        self.version = "1.0.0"
        self._plugin_manager = None

    # ---------------------------------------------------------------------
    # Infrastructure (injection du plugin manager)
    # ---------------------------------------------------------------------
    def set_plugin_manager(self, plugin_manager) -> None:
        """Injection du plugin manager fournie par le wrapper Python."""

        self._plugin_manager = plugin_manager

    # ------------------------------------------------------------------
    # API principale
    # ------------------------------------------------------------------
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée standard du plugin metasolver."""

        start_time = time.time()

        if not self._plugin_manager:
            return self._error_response("PluginManager non initialisé", start_time)

        text = (inputs.get("text") or "").strip()
        if not text:
            return self._error_response("Aucun texte fourni", start_time)

        mode = (inputs.get("mode") or "decode").lower()
        if mode not in {"detect", "decode"}:
            return self._error_response(f"Mode non supporté: {mode}", start_time)

        plugin_scope = (inputs.get("plugin_scope") or "selected").lower()
        plugin_list_raw = inputs.get("plugin_list") or ""
        enable_bruteforce = bool(inputs.get("enable_bruteforce", True))
        detect_coordinates = bool(inputs.get("detect_coordinates", True))
        max_plugins = inputs.get("max_plugins")
        try:
            max_plugins_int: Optional[int] = None if max_plugins in (None, "") else int(max_plugins)
            if max_plugins_int is not None and max_plugins_int < 0:
                max_plugins_int = None
        except (TypeError, ValueError):
            max_plugins_int = None

        explicit_plugins = self._parse_plugin_list(plugin_list_raw)

        candidates = self._collect_candidates(
            mode=mode,
            plugin_scope=plugin_scope,
            explicit_plugins=explicit_plugins,
            max_plugins=max_plugins_int,
        )

        if not candidates:
            return self._error_response("Aucun plugin disponible pour ce mode !!", start_time)

        execution_log: List[Dict[str, Any]] = []
        aggregated_results: List[Dict[str, Any]] = []
        combined_results: Dict[str, Dict[str, Any]] = {}
        failed_plugins: List[Dict[str, Any]] = []
        primary_coordinates: Optional[Dict[str, Any]] = None

        request_payload = {
            "text": text,
            "mode": mode,
            "detect_coordinates": detect_coordinates,
            "enable_gps_detection": detect_coordinates,
            "brute_force": enable_bruteforce,
            "enable_bruteforce": enable_bruteforce,
        }

        for candidate in candidates:
            plugin_name = candidate["name"]
            try:
                plugin_inputs = dict(request_payload)
                plugin_inputs.update(self._build_additional_inputs(candidate["metadata"]) )

                result = self._execute_with_fallback(plugin_name, plugin_inputs, candidate)
                execution_log.append(
                    {
                        "plugin": plugin_name,
                        "status": result.get("status"),
                        "execution_time_ms": result.get("plugin_info", {}).get("execution_time_ms")
                        or result.get("plugin_info", {}).get("execution_time"),
                    }
                )

                if result.get("status") != "success" and result.get("status") != "ok":
                    failed_plugins.append(
                        {
                            "plugin": plugin_name,
                            "reason": result.get("summary") or result.get("error", {}).get("message"),
                        }
                    )
                    continue

                results_block = result.get("results") or []
                combined_results[plugin_name] = self._build_combined_entry(result)
                combined_results[plugin_name]["plugin"] = plugin_name

                if not primary_coordinates:
                    primary_coordinates = (
                        result.get("primary_coordinates")
                        or combined_results[plugin_name].get("coordinates")
                    )

                for idx, item in enumerate(results_block):
                    enriched = dict(item)
                    parameters = dict(enriched.get("parameters") or {})
                    parameters.setdefault("plugin", plugin_name)
                    parameters.setdefault("mode", mode)
                    enriched["parameters"] = parameters
                    original_id = enriched.get("id") or f"result_{idx+1}"
                    unique_id = f"{plugin_name}::{original_id}"
                    enriched["id"] = unique_id
                    enriched.setdefault("original_id", original_id)
                    enriched.setdefault("display_id", f"{plugin_name}_{idx+1}")
                    enriched.setdefault("display_label", f"Résultat {idx+1} · {plugin_name}")
                    enriched.setdefault("plugin", plugin_name)
                    enriched.setdefault("source_plugin", plugin_name)
                    aggregated_results.append(enriched)

            except Exception as exc:  # pragma: no cover - robust contre plugins tiers
                failed_plugins.append({"plugin": plugin_name, "reason": str(exc)})
                execution_log.append({"plugin": plugin_name, "status": "error", "error": str(exc)})

        aggregated_results.sort(key=lambda item: float(item.get("confidence", 0)), reverse=True)

        status = "success" if aggregated_results else "partial_success"
        summary_message = (
            f"{len(aggregated_results)} résultat(s) collecté(s)"
            if aggregated_results
            else "Aucun plugin n'a produit de résultat exploitable"
        )

        response: Dict[str, Any] = {
            "status": status,
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": round((time.time() - start_time) * 1000, 2),
                "mode": mode,
                "executed_plugins": execution_log,
            },
            "inputs": {
                "mode": mode,
                "plugin_scope": plugin_scope,
                "requested_plugins": sorted(explicit_plugins) if explicit_plugins else None,
                "max_plugins": max_plugins_int,
                "enable_bruteforce": enable_bruteforce,
                "detect_coordinates": detect_coordinates,
            },
            "results": aggregated_results,
            "combined_results": combined_results,
            "primary_coordinates": primary_coordinates,
            "failed_plugins": failed_plugins,
            "summary": summary_message,
            "summary_details": {
                "message": summary_message,
                "total_results": len(aggregated_results),
                "plugins_considered": len(candidates),
                "plugins_failed": len(failed_plugins),
            },
        }

        if not aggregated_results and failed_plugins:
            response["status"] = "error"

        return response

    # ------------------------------------------------------------------
    # Utilitaires privés
    # ------------------------------------------------------------------
    def _parse_plugin_list(self, raw: str) -> List[str]:
        if not raw:
            return []
        items = [item.strip().lower() for item in raw.split(",")]
        return [item for item in items if item]

    def _collect_candidates(
        self,
        *,
        mode: str,
        plugin_scope: str,
        explicit_plugins: List[str],
        max_plugins: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Sélectionne les plugins à exécuter en fonction des paramètres."""

        include_disabled = plugin_scope == "all"
        all_plugins = self._plugin_manager.list_plugins(enabled_only=not include_disabled) or []

        fallback_names = FALLBACK_PLUGIN_PIPELINE.get(mode, [])
        if fallback_names:
            known_names = {entry.get("name") for entry in all_plugins}
            for fallback_name in fallback_names:
                if fallback_name not in known_names:
                    all_plugins.append({"name": fallback_name, "enabled": True, "_fallback": True})

        candidates: List[Dict[str, Any]] = []
        explicit_set = set(explicit_plugins)

        for plugin_entry in all_plugins:
            name = plugin_entry.get("name")
            if not name:
                continue
            if name == self.name:
                continue
            if explicit_set and name not in explicit_set:
                continue
            if not plugin_entry.get("enabled", True) and not include_disabled:
                continue

            info = self._plugin_manager.get_plugin_info(name) or {}
            metadata = info.get("metadata") or {}

            if plugin_entry.get("_fallback"):
                metadata = {
                    "capabilities": {"analyze": True, "decode": True},
                    "input_types": {},
                    "_fallback": True,
                }
            capabilities = metadata.get("capabilities") or {}

            if mode == "detect" and not capabilities.get("analyze"):
                continue
            if mode == "decode" and not capabilities.get("decode"):
                continue

            candidates.append({"name": name, "metadata": metadata})

        # Si liste explicite fournie, conserver l'ordre utilisateur
        if explicit_set:
            order = {plugin: idx for idx, plugin in enumerate(explicit_plugins)}
            candidates.sort(key=lambda item: order.get(item["name"], len(order)))
        else:
            candidates.sort(key=lambda item: item["name"])

        if max_plugins is not None:
            candidates = candidates[:max_plugins]

        if not candidates and fallback_names:
            candidates = [
                {
                    "name": name,
                    "metadata": {
                        "capabilities": {"analyze": True, "decode": True},
                        "input_types": {},
                        "_fallback": True,
                    },
                }
                for name in fallback_names
            ]

        return candidates

    def _execute_with_fallback(
        self,
        plugin_name: str,
        inputs: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Tente d'exécuter via le PluginManager puis bascule sur un chargement direct."""

        manager_result = self._plugin_manager.execute_plugin(plugin_name, inputs)

        summary_text = (manager_result or {}).get("summary") or ""
        is_unavailable = (
            not manager_result
            or manager_result.get("status") == "error"
            and (
                "non disponible" in summary_text.lower()
                or "introuvable" in summary_text.lower()
                or "non trouvé" in summary_text.lower()
            )
        )

        if not is_unavailable:
            return manager_result or self._error_response("Aucun résultat retourné", time.time())

        if not candidate.get("metadata", {}).get("_fallback"):
            return manager_result or self._error_response("Plugin indisponible", time.time())

        direct_result = self._execute_plugin_direct(plugin_name, inputs)

        if direct_result:
            return direct_result

        return manager_result or self._error_response("Échec exécution fallback", time.time())

    def _execute_plugin_direct(self, plugin_name: str, inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Charge et exécute directement un plugin depuis son répertoire officiel."""

        plugins_root = getattr(self._plugin_manager, "plugins_dir", None)
        if not plugins_root:
            return None

        plugin_dir = Path(plugins_root) / "official" / plugin_name
        plugin_json = plugin_dir / "plugin.json"

        if not plugin_json.exists():
            return None

        try:
            with plugin_json.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
        except Exception:
            return None

        try:
            PluginMetadata, create_plugin_wrapper = _lazy_import_wrappers()
            wrapper_metadata = PluginMetadata(
                name=metadata["name"],
                version=metadata.get("version", "1.0.0"),
                plugin_type=metadata.get("plugin_type"),
                entry_point=metadata.get("entry_point", "main.py"),
                path=str(plugin_dir),
                timeout_seconds=int(metadata.get("timeout_seconds", 30)),
            )

            wrapper = create_plugin_wrapper(
                metadata.get("plugin_type"),
                wrapper_metadata,
                plugin_manager=self._plugin_manager,
            )

            if not wrapper:
                return None

            if not wrapper.initialize():
                return None

            return wrapper.execute(inputs)

        except Exception:
            return None

    def _build_additional_inputs(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Prépare les champs additionnels à transmettre à un plugin cible."""

        extras: Dict[str, Any] = {}
        input_types = metadata.get("input_types") or {}

        # Si le plugin cible accepte un champ detect_coordinates
        if "detect_coordinates" in input_types:
            extras["detect_coordinates"] = True
        elif "enable_gps_detection" in input_types:
            extras["enable_gps_detection"] = True

        return extras

    def _build_combined_entry(self, plugin_result: Dict[str, Any]) -> Dict[str, Any]:
        """Synthétise les informations d'un plugin exécuté."""

        combined: Dict[str, Any] = {}
        results = plugin_result.get("results") or []
        if results:
            first = results[0]
            combined["decoded_text"] = first.get("text_output")
            if "confidence" in first:
                combined["confidence"] = first.get("confidence")
            if "coordinates" in first:
                combined["coordinates"] = first.get("coordinates")
        summary = plugin_result.get("summary")
        if summary:
            combined["summary"] = summary
        return combined

    def _error_response(self, message: str, start_time: float) -> Dict[str, Any]:
        return {
            "status": "error",
            "summary": message,
            "results": [],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": round((time.time() - start_time) * 1000, 2),
            },
        }


__all__ = ["MetaSolverPlugin"]
