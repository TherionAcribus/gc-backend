"""Plugin metasolver pour orchestrer l'exécution de plusieurs plugins MysterAI.

Ce plugin agit comme un "meta-plugin" : il peut lancer en séquence un ensemble de
plugins d'analyse (mode "detect") ou de décodage (mode "decode") et agréger leurs
résultats.

La sélection des plugins est **dynamique** : seuls les plugins déclarant
``"metasolver": {"eligible": true}`` dans leur ``plugin.json`` sont considérés.
Des **presets** (définis dans ``presets.json``) permettent de filtrer par tags ou
par type de charset (letters, digits, symbols, words, mixed).

Le comportement est configurable via les paramètres d'entrée définis dans
``plugin.json`` afin d'adapter la portée (preset), les options de bruteforce ou
encore la détection automatique de coordonnées.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from gc_backend.plugins.scoring import score_and_rank_results as _score_and_rank
    from gc_backend.plugins.scoring.scorer import score_text_fast as _score_fast

    _BATCH_SCORING_AVAILABLE = True
except Exception:  # pragma: no cover
    _score_and_rank = None
    _score_fast = None
    _BATCH_SCORING_AVAILABLE = False


def _lazy_import_wrappers():
    """Importe les wrappers de plugin seulement si nécessaire."""

    from gc_backend.plugins.wrappers import PluginMetadata, create_plugin_wrapper  # type: ignore

    return PluginMetadata, create_plugin_wrapper


class MetaSolverPlugin:
    """Plugin orchestrateur pour les autres plugins MysterAI."""

    def __init__(self) -> None:
        self.name = "metasolver"
        self.version = "2.0.0"
        self._plugin_manager = None
        self._presets: Optional[Dict[str, Any]] = None

    # ---------------------------------------------------------------------
    # Infrastructure (injection du plugin manager)
    # ---------------------------------------------------------------------
    def set_plugin_manager(self, plugin_manager) -> None:
        """Injection du plugin manager fournie par le wrapper Python."""

        self._plugin_manager = plugin_manager

    # ------------------------------------------------------------------
    # Presets
    # ------------------------------------------------------------------
    def _load_presets(self) -> Dict[str, Any]:
        """Charge les presets depuis presets.json (à côté de ce fichier)."""

        if self._presets is not None:
            return self._presets

        presets_path = Path(__file__).parent / "presets.json"
        try:
            with presets_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            self._presets = data.get("presets") or {}
        except Exception:
            self._presets = {}

        return self._presets

    def _get_preset_filter(self, preset_name: str) -> Dict[str, Any]:
        """Retourne le filtre d'un preset donné (vide si preset inconnu ou 'all')."""

        presets = self._load_presets()
        preset = presets.get(preset_name)
        if not preset:
            return {}
        return preset.get("filter") or {}

    def get_available_presets(self) -> Dict[str, Dict[str, str]]:
        """Retourne la liste des presets disponibles (label + description)."""

        presets = self._load_presets()
        return {
            name: {"label": p.get("label", name), "description": p.get("description", "")}
            for name, p in presets.items()
        }

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

        preset = (inputs.get("preset") or "all").lower()
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

        preset_filter = self._get_preset_filter(preset)

        candidates = self._collect_candidates(
            mode=mode,
            preset_filter=preset_filter,
            explicit_plugins=explicit_plugins,
            max_plugins=max_plugins_int,
        )

        if not candidates:
            return self._error_response(
                f"Aucun plugin éligible pour le mode '{mode}' avec le preset '{preset}'",
                start_time,
            )

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

        def _run_one(candidate: Dict[str, Any]) -> Dict[str, Any]:
            """Execute a single candidate plugin (thread-safe)."""
            pname = candidate["name"]
            plugin_inputs = dict(request_payload)
            plugin_inputs.update(self._build_additional_inputs(candidate["metadata"]))
            t0 = time.time()
            try:
                result = self._execute_with_fallback(pname, plugin_inputs, candidate)
                elapsed = round((time.time() - t0) * 1000, 2)
                return {"name": pname, "result": result, "elapsed_ms": elapsed, "error": None}
            except Exception as exc:
                elapsed = round((time.time() - t0) * 1000, 2)
                return {"name": pname, "result": None, "elapsed_ms": elapsed, "error": str(exc)}

        # Execute plugins in parallel (max 6 workers to avoid overloading)
        max_workers = min(6, len(candidates))
        futures_results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_run_one, c): c for c in candidates}
            for future in as_completed(futures):
                futures_results.append(future.result())

        # Re-order by original candidate priority order
        candidate_order = {c["name"]: i for i, c in enumerate(candidates)}
        futures_results.sort(key=lambda r: candidate_order.get(r["name"], 999))

        for entry in futures_results:
            plugin_name = entry["name"]
            result = entry["result"]
            error = entry["error"]

            if error or not result:
                failed_plugins.append({"plugin": plugin_name, "reason": error or "No result"})
                execution_log.append({"plugin": plugin_name, "status": "error", "error": error})
                continue

            execution_log.append(
                {
                    "plugin": plugin_name,
                    "status": result.get("status"),
                    "execution_time_ms": entry["elapsed_ms"],
                }
            )

            if result.get("status") != "success" and result.get("status") != "ok":
                reason = self._extract_summary_text(result.get("summary")) or result.get("error", {}).get("message")
                failed_plugins.append(
                    {
                        "plugin": plugin_name,
                        "reason": reason,
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
                # Override plugin confidence with text quality score
                enriched["plugin_confidence"] = enriched.get("confidence", 0)
                text_output = enriched.get("text_output", "")
                if _score_fast is not None and isinstance(text_output, str) and text_output.strip():
                    enriched["confidence"] = _score_fast(text_output)
                else:
                    # No text output → score 0
                    enriched["confidence"] = 0.0
                aggregated_results.append(enriched)

        aggregated_results.sort(key=lambda item: float(item.get("confidence", 0)), reverse=True)

        status = "success" if aggregated_results else "partial_success"
        summary_message = (
            f"{len(aggregated_results)} résultat(s) collecté(s)"
            if aggregated_results
            else "Aucun plugin n'a produit de résultat exploitable"
        )

        total_ms = round((time.time() - start_time) * 1000, 2)
        plugin_times = [e.get("execution_time_ms", 0) for e in execution_log if e.get("status") in ("success", "ok")]
        slowest_plugin = max(plugin_times) if plugin_times else 0
        avg_plugin_time = round(sum(plugin_times) / len(plugin_times), 2) if plugin_times else 0

        response: Dict[str, Any] = {
            "status": status,
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": total_ms,
                "mode": mode,
                "preset": preset,
                "executed_plugins": execution_log,
            },
            "inputs": {
                "mode": mode,
                "preset": preset,
                "preset_filter": preset_filter if preset_filter else None,
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
                "plugins_succeeded": len(candidates) - len(failed_plugins),
                "plugins_failed": len(failed_plugins),
            },
            "diagnostics": {
                "total_execution_ms": total_ms,
                "parallel_workers": max_workers,
                "slowest_plugin_ms": slowest_plugin,
                "avg_plugin_ms": avg_plugin_time,
                "sum_plugin_ms": round(sum(plugin_times), 2),
                "parallelism_speedup": round(sum(plugin_times) / total_ms, 2) if total_ms > 0 else 1.0,
                "total_raw_results": len(aggregated_results),
            },
        }

        if not aggregated_results and failed_plugins:
            response["status"] = "error"

        return response

    # ------------------------------------------------------------------
    # API streaming (SSE)
    # ------------------------------------------------------------------
    def execute_streaming(self, inputs: Dict[str, Any]):
        """Générateur qui yield des événements de progression SSE.

        Chaque élément yielded est un dict sérialisable en JSON avec un champ
        ``event`` indiquant le type :
        - ``init``       : liste des candidats, paramètres
        - ``plugin_start`` : un sous-plugin démarre
        - ``plugin_done``  : un sous-plugin a terminé (succès)
        - ``plugin_error`` : un sous-plugin a échoué
        - ``progress``     : avancement global (pourcentage, compteurs)
        - ``result``       : résultat final complet (même format que execute())
        """

        start_time = time.time()

        if not self._plugin_manager:
            yield {"event": "result", "data": self._error_response("PluginManager non initialisé", start_time)}
            return

        text = (inputs.get("text") or "").strip()
        if not text:
            yield {"event": "result", "data": self._error_response("Aucun texte fourni", start_time)}
            return

        mode = (inputs.get("mode") or "decode").lower()
        if mode not in {"detect", "decode"}:
            yield {"event": "result", "data": self._error_response(f"Mode non supporté: {mode}", start_time)}
            return

        preset = (inputs.get("preset") or "all").lower()
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
        preset_filter = self._get_preset_filter(preset)

        candidates = self._collect_candidates(
            mode=mode,
            preset_filter=preset_filter,
            explicit_plugins=explicit_plugins,
            max_plugins=max_plugins_int,
        )

        if not candidates:
            yield {"event": "result", "data": self._error_response(
                f"Aucun plugin éligible pour le mode '{mode}' avec le preset '{preset}'",
                start_time,
            )}
            return

        # Événement init
        yield {
            "event": "init",
            "data": {
                "total_plugins": len(candidates),
                "plugins": [c["name"] for c in candidates],
                "mode": mode,
                "preset": preset,
            },
        }

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

        # Build candidate index for SSE event ordering
        candidate_index = {c["name"]: i for i, c in enumerate(candidates)}

        def _run_streaming(candidate: Dict[str, Any]) -> Dict[str, Any]:
            """Execute a single plugin (thread-safe)."""
            pname = candidate["name"]
            plugin_inputs = dict(request_payload)
            plugin_inputs.update(self._build_additional_inputs(candidate["metadata"]))
            t0 = time.time()
            try:
                result = self._execute_with_fallback(pname, plugin_inputs, candidate)
                elapsed = round((time.time() - t0) * 1000, 2)
                return {"name": pname, "result": result, "elapsed_ms": elapsed, "error": None}
            except Exception as exc:
                elapsed = round((time.time() - t0) * 1000, 2)
                return {"name": pname, "result": None, "elapsed_ms": elapsed, "error": str(exc)}

        # Emit plugin_start for all candidates (they all start immediately)
        for idx_candidate, candidate in enumerate(candidates):
            yield {
                "event": "plugin_start",
                "data": {
                    "plugin": candidate["name"],
                    "index": idx_candidate,
                    "total": len(candidates),
                },
            }

        # Execute all plugins in parallel and yield events as they complete
        max_workers = min(6, len(candidates))
        completed_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_run_streaming, c): c for c in candidates}
            for future in as_completed(futures):
                entry = future.result()
                plugin_name = entry["name"]
                result = entry["result"]
                error = entry["error"]
                exec_time_ms = entry["elapsed_ms"]
                idx_candidate = candidate_index.get(plugin_name, 0)

                if error or not result:
                    failed_plugins.append({"plugin": plugin_name, "reason": error or "No result"})
                    execution_log.append({"plugin": plugin_name, "status": "error", "error": error})
                    yield {
                        "event": "plugin_error",
                        "data": {
                            "plugin": plugin_name,
                            "index": idx_candidate,
                            "total": len(candidates),
                            "reason": error or "No result",
                            "execution_time_ms": exec_time_ms,
                        },
                    }
                elif result.get("status") != "success" and result.get("status") != "ok":
                    reason = self._extract_summary_text(result.get("summary")) or result.get("error", {}).get("message")
                    failed_plugins.append({"plugin": plugin_name, "reason": reason})
                    execution_log.append({
                        "plugin": plugin_name,
                        "status": result.get("status"),
                        "execution_time_ms": exec_time_ms,
                    })
                    yield {
                        "event": "plugin_error",
                        "data": {
                            "plugin": plugin_name,
                            "index": idx_candidate,
                            "total": len(candidates),
                            "reason": reason,
                            "execution_time_ms": exec_time_ms,
                        },
                    }
                else:
                    execution_log.append({
                        "plugin": plugin_name,
                        "status": result.get("status"),
                        "execution_time_ms": exec_time_ms,
                    })
                    results_block = result.get("results") or []
                    combined_results[plugin_name] = self._build_combined_entry(result)
                    combined_results[plugin_name]["plugin"] = plugin_name

                    if not primary_coordinates:
                        primary_coordinates = (
                            result.get("primary_coordinates")
                            or combined_results[plugin_name].get("coordinates")
                        )

                    plugin_aggregated = []
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
                        # Override plugin confidence with text quality score
                        enriched["plugin_confidence"] = enriched.get("confidence", 0)
                        text_output = enriched.get("text_output", "")
                        if _score_fast is not None and isinstance(text_output, str) and text_output.strip():
                            enriched["confidence"] = _score_fast(text_output)
                        else:
                            # No text output → score 0
                            enriched["confidence"] = 0.0
                        aggregated_results.append(enriched)
                        plugin_aggregated.append(enriched)

                    yield {
                        "event": "plugin_done",
                        "data": {
                            "plugin": plugin_name,
                            "index": idx_candidate,
                            "total": len(candidates),
                            "execution_time_ms": exec_time_ms,
                            "result_count": len(results_block),
                            "results": plugin_aggregated,
                            "combined": combined_results[plugin_name],
                        },
                    }

                # Événement progress
                completed_count += 1
                yield {
                    "event": "progress",
                    "data": {
                        "completed": completed_count,
                        "total": len(candidates),
                        "percentage": round(completed_count / len(candidates) * 100, 1),
                        "results_so_far": len(aggregated_results),
                        "failures_so_far": len(failed_plugins),
                        "elapsed_ms": round((time.time() - start_time) * 1000, 2),
                    },
                }

        aggregated_results.sort(key=lambda item: float(item.get("confidence", 0)), reverse=True)

        status = "success" if aggregated_results else "partial_success"
        summary_message = (
            f"{len(aggregated_results)} résultat(s) collecté(s)"
            if aggregated_results
            else "Aucun plugin n'a produit de résultat exploitable"
        )

        total_ms = round((time.time() - start_time) * 1000, 2)
        plugin_times_s = [e.get("execution_time_ms", 0) for e in execution_log if e.get("status") in ("success", "ok")]
        slowest_s = max(plugin_times_s) if plugin_times_s else 0
        avg_s = round(sum(plugin_times_s) / len(plugin_times_s), 2) if plugin_times_s else 0

        response: Dict[str, Any] = {
            "status": status,
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": total_ms,
                "mode": mode,
                "preset": preset,
                "executed_plugins": execution_log,
            },
            "inputs": {
                "mode": mode,
                "preset": preset,
                "preset_filter": preset_filter if preset_filter else None,
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
                "plugins_succeeded": len(candidates) - len(failed_plugins),
                "plugins_failed": len(failed_plugins),
            },
            "diagnostics": {
                "total_execution_ms": total_ms,
                "parallel_workers": max_workers,
                "slowest_plugin_ms": slowest_s,
                "avg_plugin_ms": avg_s,
                "sum_plugin_ms": round(sum(plugin_times_s), 2),
                "parallelism_speedup": round(sum(plugin_times_s) / total_ms, 2) if total_ms > 0 else 1.0,
                "total_raw_results": len(aggregated_results),
            },
        }

        if not aggregated_results and failed_plugins:
            response["status"] = "error"

        yield {"event": "result", "data": response}

    # ------------------------------------------------------------------
    # Utilitaires privés
    # ------------------------------------------------------------------
    def _parse_plugin_list(self, raw: str) -> List[str]:
        if not raw:
            return []
        items = [item.strip().lower() for item in raw.split(",")]
        return [item for item in items if item]

    @staticmethod
    def _matches_preset_filter(
        metasolver_meta: Dict[str, Any],
        preset_filter: Dict[str, Any],
    ) -> bool:
        """Vérifie si les métadonnées metasolver d'un plugin correspondent au filtre du preset.

        Un filtre vide (preset "all") accepte tout plugin éligible.
        Clés de filtre supportées :
        - ``tags`` (list[str])          : le plugin doit posséder **au moins un** des tags listés.
        - ``input_charset`` (list[str]) : le ``input_charset`` du plugin doit être dans la liste.
        """

        if not preset_filter:
            return True

        # Filtre par tags (OR : au moins un tag commun)
        filter_tags = preset_filter.get("tags")
        if filter_tags:
            plugin_tags = set(metasolver_meta.get("tags") or [])
            if not plugin_tags.intersection(filter_tags):
                return False

        # Filtre par input_charset
        filter_charsets = preset_filter.get("input_charset")
        if filter_charsets:
            plugin_charset = metasolver_meta.get("input_charset", "")
            if plugin_charset not in filter_charsets:
                return False

        return True

    def _collect_candidates(
        self,
        *,
        mode: str,
        preset_filter: Dict[str, Any],
        explicit_plugins: List[str],
        max_plugins: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Sélectionne les plugins à exécuter.

        Logique :
        1. Lister tous les plugins activés.
        2. Pour chaque plugin, récupérer ses métadonnées complètes.
        3. Ne retenir que ceux qui déclarent ``metasolver.eligible = true``.
        4. Filtrer par capabilities (analyze/decode) selon le mode.
        5. Appliquer le filtre du preset (tags / input_charset).
        6. Si une liste explicite est fournie, ne garder que ces plugins (en
           conservant l'ordre utilisateur).
        7. Trier par priorité décroissante (champ ``metasolver.priority``).
        8. Limiter au ``max_plugins`` demandé.
        """

        all_plugins = self._plugin_manager.list_plugins(enabled_only=True) or []

        candidates: List[Dict[str, Any]] = []
        explicit_set = set(explicit_plugins)

        for plugin_entry in all_plugins:
            name = plugin_entry.get("name")
            if not name or name == self.name:
                continue

            # Si liste explicite, ne garder que les plugins demandés
            if explicit_set and name not in explicit_set:
                continue

            # Récupérer les métadonnées complètes
            info = self._plugin_manager.get_plugin_info(name) or {}
            metadata = info.get("metadata") or {}

            # Vérifier l'éligibilité metasolver
            metasolver_meta = metadata.get("metasolver") or {}
            if not metasolver_meta.get("eligible"):
                # Si le plugin est explicitement demandé, on l'accepte quand même
                if not explicit_set:
                    continue

            # Vérifier les capabilities pour le mode demandé
            capabilities = metadata.get("capabilities") or {}
            if mode == "detect" and not capabilities.get("analyze"):
                continue
            if mode == "decode" and not capabilities.get("decode"):
                continue

            # Appliquer le filtre du preset (sauf si liste explicite)
            if not explicit_set and not self._matches_preset_filter(metasolver_meta, preset_filter):
                continue

            priority = metasolver_meta.get("priority", 50)
            candidates.append({
                "name": name,
                "metadata": metadata,
                "priority": priority,
            })

        # Tri
        if explicit_set:
            # Conserver l'ordre utilisateur
            order = {plugin: idx for idx, plugin in enumerate(explicit_plugins)}
            candidates.sort(key=lambda item: order.get(item["name"], len(order)))
        else:
            # Tri par priorité décroissante puis par nom
            candidates.sort(key=lambda item: (-item["priority"], item["name"]))

        if max_plugins is not None and max_plugins > 0:
            candidates = candidates[:max_plugins]

        return candidates

    def _execute_with_fallback(
        self,
        plugin_name: str,
        inputs: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Tente d'exécuter via le PluginManager puis bascule sur un chargement direct."""

        manager_result = self._plugin_manager.execute_plugin(plugin_name, inputs)

        summary_text = self._extract_summary_text((manager_result or {}).get("summary"))
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

        # Tentative de chargement direct depuis le répertoire officiel
        direct_result = self._execute_plugin_direct(plugin_name, inputs)

        if direct_result:
            return direct_result

        return manager_result or self._error_response("Plugin indisponible", time.time())

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

    @staticmethod
    def _extract_summary_text(summary: Any) -> str:
        """Extrait un texte lisible depuis un champ summary (str ou dict)."""
        if isinstance(summary, dict):
            return str(summary.get("message", ""))
        if summary is None:
            return ""
        return str(summary)

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
