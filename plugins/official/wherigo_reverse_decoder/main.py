"""
Plugin Wherigo Reverse Decoder pour MysterAI.

Ce plugin permet de décoder les coordonnées d'un Wherigo Reverse à partir
de trois nombres de 6 chiffres et de les convertir en format WGS84 compatible
avec Geocaching.
"""

import re
import time
from typing import Dict, Any, List


class WherigoReverseDecoderPlugin:
    """
    Plugin pour décoder les coordonnées d'un Wherigo Reverse
    et les convertir en format WGS84 compatible avec Geocaching.
    """
    
    def __init__(self):
        self.name = "wherigo_reverse_decoder"
        self.version = "1.0.0"
    
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Point d'entrée principal du plugin.
        
        Args:
            inputs (dict): Paramètres d'entrée contenant :
                - text (str): Texte contenant les nombres de 6 chiffres
                - strict (bool): Mode strict (exactement 6 chiffres) ou flexible
                - embedded (bool): Texte avec code intégré
                
        Returns:
            dict: Résultat au format standardisé
        """
        start_time = time.time()
        
        # Extraction des paramètres
        text = inputs.get("text", "").strip()
        strict_mode = inputs.get("strict", True)
        embedded_mode = inputs.get("embedded", False)
        
        # Initialisation du format standardisé
        result = {
            "status": "success",
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time": 0
            },
            "inputs": inputs,
            "results": [],
            "summary": {
                "total_results": 0
            }
        }

        if not text:
            result["status"] = "error"
            result["summary"]["message"] = "Aucun texte fourni pour l'analyse."
            return result

        try:
            # Recherche des fragments
            fragments = self.find_fragments(text, strict_mode)
            is_match = len(fragments) >= 3
            score = min(1.0, len(fragments) / 3.0) if is_match else 0.0

            if not is_match:
                result["status"] = "error"
                result["summary"]["message"] = "Impossible de trouver trois nombres de 6 chiffres dans l'entrée."
                result["results"] = []
                return result

            # Utiliser les 3 premiers nombres trouvés
            a, b, c = fragments[:3]
            latitude, longitude = self._decode_coordinates(a, b, c)
            formatted_coords = self._convert_to_wgs84(latitude, longitude)

            # Calcul de la confiance
            confidence = 1.0 if strict_mode else score

            # Ajout du résultat au format standardisé
            result["results"].append({
                "id": "result_1",
                "text_output": formatted_coords,
                "confidence": confidence,
                "parameters": {
                    "mode": "decode",
                    "strict": strict_mode,
                    "embedded": embedded_mode
                },
                "metadata": {
                    "fragments": fragments,
                    "score": score,
                    "latitude": latitude,
                    "longitude": longitude
                }
            })
            result["summary"]["total_results"] = 1
            result["summary"]["best_result_id"] = "result_1"
            result["summary"]["message"] = "Décodage réussi."

        except Exception as e:
            result["status"] = "error"
            result["summary"]["message"] = f"Erreur lors du décodage : {str(e)}"
            result["results"] = []

        result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return result

    def find_fragments(self, text: str, strict: bool = True) -> List[str]:
        """
        Cherche des fragments de texte qui pourraient être des nombres de 6 chiffres.
        
        Args:
            text: Texte à analyser
            strict: Mode strict (True) ou smooth (False)
            
        Returns:
            Liste des fragments trouvés (nombres de 6 chiffres)
        """
        if strict:
            # En mode strict, on cherche uniquement des nombres de exactement 6 chiffres
            return re.findall(r'\b\d{6}\b', text)
        else:
            # En mode smooth, on est plus flexible sur les caractères autour
            return re.findall(r'\d{6}', text)

    def check_code(self, text: str, strict: bool = True, allowed_chars: List[str] = None, embedded: bool = False) -> Dict[str, Any]:
        """
        Vérifie si le texte contient du code Wherigo Reverse valide.
        
        Args:
            text: Texte à analyser
            strict: Mode strict (True) ou smooth (False)
            allowed_chars: Liste de caractères autorisés en plus des caractères du code
            embedded: True si le texte peut contenir du code intégré
            
        Returns:
            Un dictionnaire contenant:
            - is_match: True si du code valide a été trouvé
            - fragments: Liste des fragments de code trouvés
            - score: Score de confiance (0.0 à 1.0)
        """
        fragments = self.find_fragments(text, strict)
        
        if embedded:
            # En mode embedded, on cherche au moins 3 nombres dans le texte
            is_match = len(fragments) >= 3
            score = min(1.0, len(fragments) / 3.0) if is_match else 0.0
        else:
            # En mode non-embedded, tout le texte doit être des nombres
            if strict:
                # En mode strict, on doit avoir exactement 3 nombres
                pattern = r'^\s*(\d{6})\s+(\d{6})\s+(\d{6})\s*$'
                match = re.search(pattern, text)
                is_match = bool(match)
                score = 1.0 if is_match else 0.0
            else:
                # En mode smooth, on cherche au moins 3 nombres dans le texte
                is_match = len(fragments) >= 3
                # Score basé sur le ratio de chiffres dans le texte
                total_digits = sum(len(frag) for frag in fragments)
                total_chars = len(''.join(c for c in text if c.isdigit() or c.isalpha()))
                score = total_digits / max(1, total_chars) if total_chars > 0 else 0.0
        
        return {
            "is_match": is_match,
            "fragments": fragments[:3] if len(fragments) >= 3 else fragments,
            "score": score
        }

    def decode_fragments(self, text: str, fragments: List[str]) -> str:
        """
        Décode uniquement les fragments valides dans leur contexte original.
        
        Args:
            text: Texte original contenant les fragments
            fragments: Liste des fragments à décoder
            
        Returns:
            Texte avec les fragments décodés
        """
        if len(fragments) >= 3:
            a, b, c = fragments[:3]
            latitude, longitude = self._decode_coordinates(a, b, c)
            formatted_coords = self._convert_to_wgs84(latitude, longitude)
            return formatted_coords
        return "Pas assez de fragments pour décoder"

    def _decode_coordinates(self, a: str, b: str, c: str) -> tuple:
        """
        Transforme trois nombres en coordonnées géographiques.
        :param a: Premier nombre (str)
        :param b: Deuxième nombre (str)
        :param c: Troisième nombre (str)
        :return: Tuple (latitude, longitude)
        """
        a = int(a)
        b = int(b)
        c = int(c)

        # Déterminer les signes pour lat et lon
        sign_map = {
            1: (1, 1),
            2: (-1, 1),
            3: (1, -1),
            4: (-1, -1)
        }
        lat_sign, lon_sign = sign_map.get((a % 1000 - a % 100) // 100, (0, 0))

        if lat_sign == 0 or lon_sign == 0:
            return 0, 0

        # Calculer la latitude
        if ((c % 100000 - c % 10000) // 10000 + (c % 100 - c % 10) // 10) % 2 == 0:
            lat = lat_sign * (
                (a % 10000 - a % 1000) / 100 +
                (b % 100 - b % 10) / 10 +
                (b % 100000 - b % 10000) / 100000 +
                (c % 1000 - c % 100) / 10000 +
                (a % 1000000 - a % 100000) / 100000000 +
                (c % 100 - c % 10) / 100000 +
                a % 10 * 1.0E-5
            )
        else:
            lat = lat_sign * (
                (b % 1000000 - b % 100000) / 10000 +
                a % 10 +
                (a % 10000 - a % 1000) / 10000 +
                (c % 1000000 - c % 100000) / 10000000 +
                (c % 1000 - c % 100) / 100000 +
                (c % 100 - c % 10) / 100000 +
                (a % 1000000 - a % 100000) / 10000000000
            )

        # Calculer la longitude
        if ((c % 100000 - c % 10000) // 10000 + (c % 100 - c % 10) // 10) % 2 == 0:
            lon = lon_sign * (
                (a % 100000 - a % 10000) / 100 +
                (c % 1000000 - c % 100000) / 10000 +
                c % 10 +
                (b % 1000 - b % 100) / 1000 +
                (b % 1000000 - b % 100000) / 10000000 +
                (a % 100 - a % 10) / 10000 +
                (c % 100000 - c % 10000) / 100000000 +
                b % 10 * 1.0E-5
            )
        else:
            lon = lon_sign * (
                (b % 100 - b % 10) * 10 +
                c % 10 * 10 +
                (a % 100 - a % 10) / 10 +
                (a % 100000 - a % 10000) / 100000 +
                (b % 1000 - b % 100) / 10000 +
                b % 10 * 0.001 +
                (c % 100000 - c % 10000) / 100000000 +
                (b % 100000 - b % 10000) / 1000000000
            )

        return lat, lon

    def _convert_to_wgs84(self, lat: float, lon: float) -> str:
        """
        Convertit les coordonnées en format Geocaching standard.
        :param lat: Latitude brute en degrés décimaux
        :param lon: Longitude brute en degrés décimaux
        :return: Coordonnées formatées au format Geocaching
        """
        # Convertir les degrés décimaux en format degrés et minutes décimales
        lat_deg = int(abs(lat))
        lat_min = (abs(lat) - lat_deg) * 60
        lon_deg = int(abs(lon))
        lon_min = (abs(lon) - lon_deg) * 60
        
        # Déterminer les hémisphères
        lat_hem = "N" if lat >= 0 else "S"
        lon_hem = "E" if lon >= 0 else "W"
        
        # Formater la chaîne au format attendu: "N dd° mm.mmm E ddd° mm.mmm"
        coordinate_str = f"{lat_hem} {lat_deg:02d}° {lat_min:06.3f} {lon_hem} {lon_deg:03d}° {lon_min:06.3f}"
        
        return coordinate_str


plugin = WherigoReverseDecoderPlugin()


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibilité pour l'exécution modulaire du plugin."""
    print("inputs", inputs)
    return plugin.execute(inputs)
