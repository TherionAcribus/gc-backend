from __future__ import annotations

import re
import time
from typing import Any, Dict, List


class PiDigitsPlugin:
    def __init__(self) -> None:
        self.name = "pi_digits"
        self.version = "1.0.0"
        self.description = "Trouve les chiffres de Pi en fonction de leur position dans les décimales."
        
        # Les 10000 premières décimales de Pi (après la virgule)
        # Source: https://www.piday.org/million/
        self.pi_decimals = (
            "1415926535897932384626433832795028841971693993751058209749445923"
            "0781640628620899862803482534211706798214808651328230664709384460"
            "9550582231725359408128481117450284102701938521105559644622948954"
            "9303819644288109756659334461284756482337867831652712019091456485"
            "6692346034861045432664821339360726024914127372458700660631558817"
            "4881520920962829254091715364367892590360011330530548820466521384"
            "1469519415116094330572703657595919530921861173819326117931051185"
            "4807446237996274956735188575272489122793818301194912983367336244"
            "0656643086021394946395224737190702179860943702770539217176293176"
            "7523846748184676694051320005681271452635608277857713427577896091"
            "7363717872146844090122495343014654958537105079227968925892354201"
            "9956112129021960864034418159813629774771309960518707211349999998"
            "3729780499510597317328160963185950244594553469083026425223082533"
            "4468503526193118817101000313783875288658753320838142061717766914"
            "7303598253490428755468731159562863882353787593751957781857780532"
            "171226806613001927876611195909216420198"
        )
        
        self.max_position = len(self.pi_decimals)

    def get_pi_digit(self, position: int) -> str | None:
        """
        Récupère le chiffre de Pi à la position donnée (1-indexed).
        
        Args:
            position: Position dans les décimales de Pi (1 = première décimale = 1)
        
        Returns:
            Le chiffre à cette position, ou None si la position est invalide
        """
        if position < 1 or position > self.max_position:
            return None
        return self.pi_decimals[position - 1]

    def parse_positions(self, text: str, allowed_chars: str = None) -> List[int]:
        """
        Parse le texte pour extraire les positions (nombres entiers).
        Supporte les séparateurs: espaces, virgules, points-virgules, retours à la ligne.
        
        Args:
            text: Texte contenant les positions
            allowed_chars: Caractères à ignorer (par défaut: espaces, tabulations, retours à la ligne, points, degrés, cardinaux)
        """
        if allowed_chars is None:
            allowed_chars = " \t\r\n.°NSEW"
        
        # Créer un pattern regex qui capture les nombres et utilise les caractères autorisés comme séparateurs
        # On remplace chaque caractère autorisé par un espace pour séparer les nombres
        cleaned_text = text
        for char in allowed_chars:
            # Remplacer chaque caractère autorisé par un espace pour séparer les nombres
            cleaned_text = cleaned_text.replace(char, ' ')
        
        # Ajouter les virgules et points-virgules comme séparateurs aussi
        cleaned_text = cleaned_text.replace(',', ' ').replace(';', ' ').replace(':', ' ').replace('-', ' ')
        
        # Extraire tous les nombres du texte nettoyé
        # On utilise une regex pour extraire uniquement les séquences de chiffres
        number_pattern = re.compile(r'\d+')
        matches = number_pattern.findall(cleaned_text)
        
        positions = []
        for match in matches:
            try:
                pos = int(match)
                if pos > 0:  # Ignorer les positions négatives ou nulles
                    positions.append(pos)
            except ValueError:
                continue
        
        return positions

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        
        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()
        output_format = str(inputs.get("format", "digits_only")).lower()
        allowed_chars = inputs.get("allowed_chars", " \t\r\n.°NSEW")
        
        standardized_response = {
            "status": "success",
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time": 0,
            },
            "inputs": inputs.copy(),
            "results": [],
            "summary": {
                "best_result_id": None,
                "total_results": 0,
                "message": "",
            },
        }
        
        if not text:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = "Aucun texte fourni à traiter."
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response
        
        if mode != "decode":
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Mode non supporté: {mode}. Seul 'decode' est disponible."
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response
        
        try:
            # Parser les positions
            positions = self.parse_positions(text, allowed_chars)
            
            if not positions:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = "Aucune position valide trouvée dans le texte."
                standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                return standardized_response
            
            # Récupérer les chiffres de Pi
            results_data = []
            valid_count = 0
            invalid_positions = []
            
            for pos in positions:
                digit = self.get_pi_digit(pos)
                if digit is not None:
                    results_data.append({"position": pos, "digit": digit})
                    valid_count += 1
                else:
                    invalid_positions.append(pos)
            
            if not results_data:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = (
                    f"Aucune position valide. Les positions doivent être entre 1 et {self.max_position}."
                )
                standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                return standardized_response
            
            # Formater la sortie selon le format demandé
            if output_format == "digits_only":
                text_output = "".join([item["digit"] for item in results_data])
            elif output_format == "positions_and_digits":
                text_output = " ".join([f"{item['position']}={item['digit']}" for item in results_data])
            else:  # detailed
                lines = [f"Position {item['position']}: {item['digit']}" for item in results_data]
                text_output = "\n".join(lines)
            
            # Construire les métadonnées
            metadata = {
                "total_positions": len(positions),
                "valid_positions": valid_count,
                "invalid_positions": invalid_positions if invalid_positions else None,
                "max_available_position": self.max_position,
                "positions_data": results_data,
            }
            
            # Message de résumé
            summary_msg = f"{valid_count} chiffre(s) de Pi trouvé(s)"
            if invalid_positions:
                summary_msg += f" ({len(invalid_positions)} position(s) invalide(s) ignorée(s))"
            
            standardized_response["results"].append({
                "id": "result_1",
                "text_output": text_output,
                "confidence": 1.0,
                "parameters": {
                    "mode": "decode",
                    "format": output_format,
                },
                "metadata": metadata,
            })
            
            standardized_response["summary"].update({
                "best_result_id": "result_1",
                "total_results": 1,
                "message": summary_msg,
            })
            
        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur pendant le traitement : {exc}"
        
        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return PiDigitsPlugin().execute(inputs)
