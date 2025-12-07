from typing import Dict, Any
from loguru import logger
import re

# Tente d'importer la fonction de détection depuis le backend
try:
    from gc_backend.blueprints.coordinates import detect_gps_coordinates
except ImportError:
    # Fallback si l'import échoue (ex: structure différente)
    logger.warning("Import direct de detect_gps_coordinates a échoué, tentative via app context ou mock")
    detect_gps_coordinates = None

class CoordinatesFinderPlugin:
    def __init__(self):
        self.name = "coordinates_finder"
        self.description = "Recherche de coordonnées GPS dans le texte"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = inputs.get('text', '')
        
        if not text:
            return {
                "status": "success", 
                "summary": "Aucun texte fourni",
                "results": []
            }
        
        # Nettoyage basique du HTML pour la détection pure
        # (On garde le texte visible seulement pour éviter de matcher des styles css etc)
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(text, 'html.parser')
            clean_text = soup.get_text(separator=' ', strip=True)
        except Exception:
            clean_text = text

        results = []
        
        if detect_gps_coordinates:
            # On utilise la fonction centralisée
            # Elle retourne un dict {exist: bool, ddm_lat: ..., ddm_lon: ..., decimal_latitude: ...}
            # On peut l'appeler plusieurs fois ou elle gère tout le texte ?
            # detect_gps_coordinates analyse tout le texte et retourne la PREMIÈRE occurrence trouvée.
            # Pour une analyse de page complète, on voudrait peut-être toutes les occurrences.
            # Mais detect_gps_coordinates ne renvoie qu'un résultat unique.
            
            # TODO: Améliorer detect_gps_coordinates pour retourner toutes les occurrences ?
            # Pour l'instant on l'utilise telle quelle.
            
            detection = detect_gps_coordinates(clean_text)
            
            if detection and detection.get('exist'):
                # On formate le résultat pour l'interface d'analyse
                res = {
                    "id": "coord_1",
                    "text_output": f"Coordonnées détectées : {detection.get('ddm')}",
                    "confidence": detection.get('confidence', 0.8),
                    "coordinates": detection, # Structure complète
                    "decimal_latitude": detection.get('decimal_latitude'),
                    "decimal_longitude": detection.get('decimal_longitude')
                }
                results.append(res)
        
        return {
            "status": "success",
            "summary": f"{len(results)} coordonnées trouvées",
            "results": results,
            "primary_coordinates": results[0]["coordinates"] if results else None
        }

plugin = CoordinatesFinderPlugin()

def execute(inputs):
    return plugin.execute(inputs)


