"""
Plugin Formula Parser
Détecte et parse les formules de coordonnées GPS avec variables.

Supporte plusieurs formats :
- Standard : N 47° 5E.FTN E 006° 5A.JVF
- Avec espaces : N 48° 41.E D B E 006° 09. F C (A / 2)
- Avec opérations : N49°18.(B-A)(B-C-F)(D+E) E006°16.(C+F)(D+F)(C+D)
"""

import re
from typing import Dict, Any, List, Optional


class FormulaParserPlugin:
    """Plugin pour parser des formules de coordonnées GPS dans un texte."""
    
    def __init__(self):
        self.name = "formula_parser"
        self.version = "1.0.0"
        self.description = "Détecte et parse les coordonnées/formules GPS dans un texte"
    
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute le plugin pour détecter les formules de coordonnées.
        
        Args:
            inputs: Dictionnaire contenant 'text' (le texte à analyser)
        
        Returns:
            Dictionnaire avec status, results, et summary
            Format de retour :
            {
                "status": "success",
                "results": [
                    {
                        "id": "result_1",
                        "north": "N49°18.(B-A)(B-C-F)(D+E)",
                        "east": "E006°16.(C+F)(D+F)(C+D)",
                        "source": "text",
                        "text_output": "N49°18.(B-A)(B-C-F)(D+E) E006°16.(C+F)(D+F)(C+D)",
                        "confidence": 0.9
                    }
                ],
                "summary": "1 formule(s) détectée(s)"
            }
        """
        text = inputs.get('text', '')
        
        if not text:
            return {
                "status": "error",
                "error": {"message": "Aucun texte fourni"},
                "results": [],
                "summary": "Erreur : texte vide"
            }
        
        # Détection des coordonnées
        coordinates = []
        
        # Cas particulier : format avec espaces
        # N 48° 41.E D B E 006° 09. F C (A / 2)
        special_pattern = r'N\s+\d{1,2}°\s+\d{1,2}\.\s*[A-Z][\s\n]*[A-Z][\s\n]*[A-Z][\s\n]*E\s+\d{1,3}°\s+\d{1,2}\.\s+[A-Z]\s+[A-Z]\s+\([A-Z]\s*/\s*\d+\)'
        match = re.search(special_pattern, text, re.DOTALL)
        
        if match:
            # Format trouvé, le traiter spécifiquement
            full_text = match.group(0)
            
            # Séparation Nord/Est
            if re.search(r'E\s+\d{1,3}°', full_text):
                parts = re.split(r'(E\s+\d{1,3}°)', full_text)
                if len(parts) >= 3:
                    north_part = parts[0].strip()
                    east_part = parts[1] + parts[2].strip()
                    
                    # Nettoyage spécifique du format Nord
                    north_clean = re.sub(
                        r'(\d{1,2}°\s+\d{1,2}\.)\s*([A-Z])\s+([A-Z])\s+([A-Z])',
                        r'\1\2\3\4',
                        north_part
                    )
                    
                    # Nettoyage spécifique du format Est
                    east_clean = re.sub(
                        r'(\d{1,3}°\s+\d{1,2}\.)\s+([A-Z])\s+([A-Z])\s+\(([A-Z])\s*/\s*(\d+)\)',
                        r'\1\2\3(\4/\5)',
                        east_part
                    )
                    
                    coordinates.append({
                        "north": north_clean,
                        "east": east_clean,
                        "source": "special_format"
                    })
        
        # Si le format spécifique n'est pas trouvé, utiliser les méthodes traditionnelles
        if not coordinates:
            north_match = self._find_north(text)
            east_match = self._find_east(text)
            
            if north_match or east_match:
                north_str = north_match.group(0) if north_match else ""
                east_str = east_match.group(0) if east_match else ""
                
                # Nettoyer les coordonnées en évitant les chevauchements
                if north_match and east_match:
                    n_start, n_end = north_match.span()
                    e_start, e_end = east_match.span()
                    if e_start < n_end:  # Chevauchement
                        north_str = north_str[:-(n_end - e_start)].strip()
                
                # Application d'un nettoyage simplifié
                north_clean = self._basic_clean(north_str)
                east_clean = self._basic_clean(east_str)
                
                coordinates.append({
                    "north": north_clean,
                    "east": east_clean,
                    "source": "standard_format"
                })
        
        # Formater les résultats
        results = []
        for idx, coord in enumerate(coordinates, start=1):
            result = {
                "id": f"result_{idx}",
                "north": coord["north"],
                "east": coord["east"],
                "source": coord.get("source", "text"),
                "text_output": f"{coord['north']} {coord['east']}",
                "confidence": 0.9 if coord["north"] and coord["east"] else 0.5
            }
            results.append(result)
        
        # Construire le résumé
        count = len(results)
        if count == 0:
            summary = "Aucune formule détectée"
            status = "success"
        elif count == 1:
            summary = "1 formule détectée"
            status = "success"
        else:
            summary = f"{count} formules détectées"
            status = "success"
        
        return {
            "status": status,
            "results": results,
            "summary": summary
        }
    
    def _basic_clean(self, coord_str: str) -> str:
        """
        Nettoyage de base pour les formats standards.
        
        Args:
            coord_str: Chaîne de coordonnées à nettoyer
        
        Returns:
            Chaîne nettoyée
        """
        if not coord_str or '.' not in coord_str:
            return coord_str
        
        # Pour le format N 48° 41.X Y Z, transformer en N 48° 41.XYZ
        result = re.sub(
            r'(\d{1,2}°\s+\d{1,2}\.)\s*([A-Z])\s+([A-Z])\s+([A-Z])',
            r'\1\2\3\4',
            coord_str
        )
        
        # Pour le format E 006° 09.X Y (Z/W), transformer en E 006° 09.XY(Z/W)
        result = re.sub(
            r'(\d{1,3}°\s+\d{1,2}\.)\s*([A-Z])\s+([A-Z])\s+\(([A-Z])\s*/\s*(\d+)\)',
            r'\1\2\3(\4/\5)',
            result
        )
        
        return result
    
    def _find_north(self, description: str) -> Optional[re.Match]:
        """
        Essaie de trouver une coordonnée Nord (ou Sud) dans le texte.
        
        Args:
            description: Texte dans lequel chercher
        
        Returns:
            Match object ou None
        """
        patterns = [
            # Format avec degrés/minutes fixes + expressions parenthésées : N49°12.(A/G-238)(I-135)(D/J-1)
            r"[NS]\s*\d{1,2}\s*°\s*\d{1,2}\.\s*(\([A-Z0-9()+*/\-\s]+\)\s*)+",

            # Format avec tokens mixtes après le point : N48°45.B(A+E)(D+C)
            # (lettres/chiffres et/ou groupes parenthésés en séquence)
            r"[NS]\s*\d{1,2}\s*°\s*\d{1,2}\.\s*(?:[A-Z0-9]+|\([A-Z0-9()+*/\-\s]+\))+",

            # Format classique : N48°12.345
            r"[NS]\s*\d{1,2}\s*°\s*\d{1,2}\.\s*\d{1,3}",
            # Format avec variables simples : N48°12.ABC
            r"[NS]\s*\d{1,2}\s*°\s*\d{1,2}\.\s*[A-Z]{1,5}(?!\s*\()",
            # Format avec opérations : N48°(A+B).(C-D)
            r"[NS]\s*\d{1,2}\s*°\s*[A-Z0-9()+*/\-\s]{1,15}\.\s*[A-Z0-9()+*/\-\s]{1,15}",
            # Format avec espaces entre lettres : N 48° 41.E D B
            r"N\s+\d{1,2}°\s+\d{1,2}\.\s*[A-Z][\s\n]*[A-Z][\s\n]*[A-Z]"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match
        return None
    
    def _find_east(self, description: str) -> Optional[re.Match]:
        """
        Essaie de trouver une coordonnée Est (ou Ouest) dans le texte.
        
        Args:
            description: Texte dans lequel chercher
        
        Returns:
            Match object ou None
        """
        patterns = [
            # Format avec degrés/minutes fixes + expressions parenthésées : E005°59.(C-B)(H-K+1)(F-E-135)
            r"[EW]\s*\d{1,3}\s*°\s*\d{1,2}\.\s*(\([A-Z0-9()+*/\-\s]+\)\s*)+",

            # Format avec tokens mixtes après le point : E002°43.C(F+C)D
            # (lettres/chiffres et/ou groupes parenthésés en séquence)
            r"[EW]\s*\d{1,3}\s*°\s*\d{1,2}\.\s*(?:[A-Z0-9]+|\([A-Z0-9()+*/\-\s]+\))+",

            # Format classique : E006°12.345
            r"[EW]\s*\d{1,3}\s*°\s*\d{1,2}\.\s*\d{1,3}",
            # Format avec variables simples : E006°12.ABC
            r"[EW]\s*\d{1,3}\s*°\s*\d{1,2}\.\s*[A-Z]{1,5}(?!\s*\()",
            # Format avec opérations : E006°(A+B).(C-D)
            r"[EW]\s*\d{1,3}\s*°\s*[A-Z0-9()+*/\-\s]{1,15}\.\s*[A-Z0-9()+*/\-\s]{1,15}",
            # Format avec espaces et parenthèses : E 006° 09. F C (A / 2)
            r"E\s+\d{1,3}°\s+\d{1,2}\.\s+[A-Z]\s+[A-Z]\s+\([A-Z]\s*/\s*\d+\)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, description, re.IGNORECASE)
            if match:
                return match
        return None


# Instance du plugin pour l'exécution
plugin = FormulaParserPlugin()


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Point d'entrée principal pour le PluginManager."""
    return plugin.execute(inputs)
