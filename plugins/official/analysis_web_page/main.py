import json
import os
import re
from loguru import logger
from typing import Dict, Any, List

class AnalysisWebPagePlugin:
    def __init__(self):
        self.name = "analysis_web_page"
        self.description = "Méta-plugin pour analyser une page de cache en lançant plusieurs plugins"
        
        # Chargement de la configuration pipeline
        self.pipeline = []
        try:
            base_dir = os.path.dirname(__file__)
            plugin_json_path = os.path.join(base_dir, "plugin.json")
            if os.path.exists(plugin_json_path):
                with open(plugin_json_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    self.pipeline = config.get("pipeline", [])
        except Exception as e:
            logger.error(f"Erreur chargement pipeline analysis_web_page: {e}")

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute l'analyse complète de la page.
        """
        logger.info(f"START analysis_web_page inputs={inputs.keys()}")

        geocache_id_raw = inputs.get('geocache_id')
        text_content = inputs.get('text', '')
        origin_coords = inputs.get('origin_coords')

        # Par défaut, on utilise le texte fourni (peut être vide)
        page_content = text_content

        # Si un identifiant de géocache est fourni, on essaie de récupérer son contenu
        if geocache_id_raw:
            try:
                # Import lazy pour éviter les cycles
                from gc_backend.database import db
                from gc_backend.geocaches.models import Geocache

                geocache_id_str = str(geocache_id_raw).strip()

                # 1) Tentative par ID numérique (clé primaire)
                geocache = None
                try:
                    geocache_id_int = int(geocache_id_str)
                except (TypeError, ValueError):
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

                if geocache:
                    # On préfère l'HTML complet si disponible, sinon la description texte
                    content = getattr(geocache, 'description_html', None) or getattr(geocache, 'description', None)
                    if content:
                        page_content = content
                        logger.info(
                            "Contenu récupéré depuis la géocache %s (%d chars)",
                            geocache_id_str,
                            len(page_content),
                        )

                    if not origin_coords:
                        origin_coords = (
                            getattr(geocache, 'coordinates_raw', None)
                            or getattr(geocache, 'original_coordinates_raw', None)
                        )
            except Exception as e:
                logger.warning(f"Impossible de récupérer la géocache {geocache_id_raw}: {e}")
                # On continue avec text_content si disponible
        
        if not page_content:
            return {
                "status": "error",
                "summary": "Aucun contenu à analyser",
                "results": []
            }

        # Dictionnaire pour stocker les résultats combinés
        combined_results = {}
        all_results_list = []
        
        # Récupération du PluginManager via l'app context ou import
        from gc_backend.blueprints.plugins import get_plugin_manager
        plugin_manager = get_plugin_manager()
        
        if not plugin_manager:
            return {
                "status": "error", 
                "summary": "PluginManager non disponible",
                "results": []
            }

        # Exécution de la pipeline
        for step in self.pipeline:
            plugin_name = step["plugin_name"]
            
            # Préparation des inputs pour le sous-plugin
            # On passe le contenu brut et l'identifiant de géocache (brut) si dispo
            plugin_inputs = {
                "text": page_content,
                "geocache_id": geocache_id_raw,
                "enable_gps_detection": True,
                "mode": "analyze"  # Mode par défaut pour ces plugins
            }

            if plugin_name == "coordinate_projection":
                stripped = re.sub(r"<[^>]+>", " ", page_content or "")
                stripped = re.sub(r"\s+", " ", stripped).strip()
                plugin_inputs["text"] = stripped
                plugin_inputs["mode"] = "decode"
                plugin_inputs["strict"] = "smooth"
                if origin_coords:
                    plugin_inputs["origin_coords"] = origin_coords
            
            # Passer les waypoints si disponibles et si le plugin est additional_waypoints_analyzer
            if plugin_name == "additional_waypoints_analyzer" and inputs.get("waypoints"):
                plugin_inputs["waypoints"] = inputs.get("waypoints")

            # Passer explicitement les images si disponibles pour le plugin de détection de QR codes
            if plugin_name == "qr_code_detector" and inputs.get("images"):
                plugin_inputs["images"] = inputs.get("images")
            
            logger.info(f"Lancement sous-plugin: {plugin_name}")
            
            # Exécution via PluginManager
            try:
                result = plugin_manager.execute_plugin(plugin_name, plugin_inputs)
                
                # Stockage structuré
                combined_results[plugin_name] = result
                
                # Aplatissement pour la liste globale de résultats
                if result and isinstance(result, dict) and "results" in result:
                    for item in result["results"]:
                        # On marque la source
                        item["source_plugin"] = plugin_name
                        all_results_list.append(item)
                        
            except Exception as e:
                logger.error(f"Erreur exécution sous-plugin {plugin_name}: {e}")
                combined_results[plugin_name] = {"error": str(e)}

        try:
            stripped = re.sub(r"<[^>]+>", " ", page_content or "")
            stripped = re.sub(r"\s+", " ", stripped).strip()
        except Exception:
            stripped = page_content

        origin_coords_obj = None
        if origin_coords:
            try:
                from gc_backend.blueprints.coordinates import detect_gps_coordinates

                oc = detect_gps_coordinates(str(origin_coords))
                if isinstance(oc, dict) and oc.get("exist") and oc.get("ddm_lat") and oc.get("ddm_lon"):
                    origin_coords_obj = {"ddm_lat": oc.get("ddm_lat"), "ddm_lon": oc.get("ddm_lon")}
            except Exception:
                origin_coords_obj = None

        try:
            logger.info("Lancement sous-plugin: written_coords_converter")
            written_inputs = {
                "text": stripped,
                "languages": "auto",
                "max_candidates": 50,
                "include_deconcat": True,
            }
            if origin_coords_obj is not None:
                written_inputs["origin_coords"] = origin_coords_obj

            written_result = plugin_manager.execute_plugin("written_coords_converter", written_inputs)
            combined_results["written_coords_converter"] = written_result
            if written_result and isinstance(written_result, dict) and "results" in written_result:
                for item in written_result["results"]:
                    item["source_plugin"] = "written_coords_converter"
                    all_results_list.append(item)
        except Exception as e:
            logger.error(f"Erreur exécution sous-plugin written_coords_converter: {e}")
            combined_results["written_coords_converter"] = {"error": str(e)}

        # Logique d'agrégation et déduplication des coordonnées
        primary_coordinates = self._aggregate_coordinates(combined_results, all_results_list)
        
        summary_msg = f"Analyse terminée avec {len(all_results_list)} résultats."
        if primary_coordinates:
            summary_msg += f" Coordonnées trouvées : {primary_coordinates.get('latitude')}, {primary_coordinates.get('longitude')}"

        return {
            "status": "success",
            "summary": summary_msg,
            "results": all_results_list,
            "combined_results": combined_results,
            "primary_coordinates": primary_coordinates
        }

    def _aggregate_coordinates(self, combined_results, all_results_list):
        """
        Logique de sélection des meilleures coordonnées parmi les résultats.
        Priorité : coordinates_finder > formula_parser > autres
        """
        priority_order = [
            'coordinates_finder',
            'formula_parser',
            'coordinate_projection',
            'written_coords_converter',
            'qr_code_detector',  # Coordonnées détectées via QR codes
            'image_alt_text_extractor',
            'color_text_detector',
        ]
        
        # On cherche d'abord dans les résultats explicites des plugins
        for name in priority_order:
            if name in combined_results:
                res = combined_results[name]
                # Si le plugin retourne une structure avec 'primary_coordinates' ou 'decimal_latitude' dans ses résultats
                if isinstance(res, dict):
                    # Cas 1 : Le plugin a déjà identifié des coordonnées principales (ex: formula_parser modifié ou coordinates_finder)
                    if 'primary_coordinates' in res and res['primary_coordinates']:
                        pc = res['primary_coordinates']
                        if isinstance(pc, dict):
                            if pc.get('latitude') is not None and pc.get('longitude') is not None:
                                return pc
                            if pc.get('decimal_latitude') is not None and pc.get('decimal_longitude') is not None:
                                return {
                                    'latitude': pc.get('decimal_latitude'),
                                    'longitude': pc.get('decimal_longitude')
                                }
                            if pc.get('ddm_lat') and pc.get('ddm_lon'):
                                try:
                                    from gc_backend.blueprints.coordinates import convert_ddm_to_decimal

                                    dec = convert_ddm_to_decimal(pc.get('ddm_lat'), pc.get('ddm_lon'))
                                    if dec.get('latitude') is not None and dec.get('longitude') is not None:
                                        return {
                                            'latitude': dec.get('latitude'),
                                            'longitude': dec.get('longitude')
                                        }
                                except Exception:
                                    pass
                        return pc
                    
                    # Cas 2 : On regarde dans la liste des résultats individuels de ce plugin
                    if 'results' in res and isinstance(res['results'], list):
                        for item in res['results']:
                            if item.get('decimal_latitude') is not None and item.get('decimal_longitude') is not None:
                                return {
                                    'latitude': item['decimal_latitude'],
                                    'longitude': item['decimal_longitude']
                                }
        
        return None

# Instance pour le chargement
plugin = AnalysisWebPagePlugin()

def execute(inputs):
    return plugin.execute(inputs)

