from typing import Dict, Any, List
from loguru import logger
from bs4 import BeautifulSoup, Tag
import re

class ColorTextDetectorPlugin:
    def __init__(self):
        self.name = "color_text_detector"
        self.description = "Détecte les textes cachés par couleur"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        html_content = inputs.get('text', '')
        
        if not html_content:
            return {"status": "success", "results": [], "summary": "Aucun texte"}

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            return {"status": "error", "summary": f"Erreur parsing HTML: {e}", "results": []}

        results = []
        
        # 1. Recherche de texte blanc explicite (souvent caché sur fond blanc par défaut)
        # color: white, color: #ffffff, color: #fff, color: rgb(255,255,255)
        white_patterns = [
            r'color\s*:\s*white',
            r'color\s*:\s*#ffffff',
            r'color\s*:\s*#fff\b',
            r'color\s*:\s*rgb\(\s*255\s*,\s*255\s*,\s*255\s*\)'
        ]
        
        # Parcourir tous les éléments avec un attribut style
        for tag in soup.find_all(attrs={"style": True}):
            style = tag.get('style', '').lower()
            text = tag.get_text(strip=True)
            
            if not text:
                continue
                
            # Détection texte blanc
            is_white = False
            for pattern in white_patterns:
                if re.search(pattern, style):
                    is_white = True
                    break
            
            if is_white:
                results.append({
                    "id": f"white_text_{len(results)}",
                    "text_output": f"Texte blanc détecté : '{text}'",
                    "hidden_text": text,
                    "method": "white_color",
                    "tag": str(tag.name),
                    "confidence": 0.9
                })
                continue # On passe au suivant pour ce tag
                
            # Détection couleur == background
            # Extraction basique des couleurs
            fg_color = self._extract_color(style, 'color')
            bg_color = self._extract_color(style, 'background-color') or self._extract_color(style, 'background')
            
            if fg_color and bg_color and fg_color == bg_color:
                results.append({
                    "id": f"same_color_{len(results)}",
                    "text_output": f"Texte ton sur ton ({fg_color}) : '{text}'",
                    "hidden_text": text,
                    "method": "same_color",
                    "tag": str(tag.name),
                    "confidence": 0.95
                })

        # 2. Recherche balises <font color="white"> (ancien HTML)
        for tag in soup.find_all('font', color=True):
            color = tag.get('color', '').lower()
            text = tag.get_text(strip=True)
            if not text:
                continue
                
            if color in ['white', '#ffffff', '#fff']:
                results.append({
                    "id": f"font_white_{len(results)}",
                    "text_output": f"Balise font blanche : '{text}'",
                    "hidden_text": text,
                    "method": "font_tag",
                    "confidence": 0.9
                })

        return {
            "status": "success",
            "summary": f"{len(results)} textes cachés potentiels détectés",
            "results": results
        }

    def _extract_color(self, style_str, prop_name):
        """Extrait la valeur d'une propriété CSS simple"""
        pattern = re.compile(rf'{prop_name}\s*:\s*([^;]+)')
        match = pattern.search(style_str)
        if match:
            return match.group(1).strip().lower()
        return None

plugin = ColorTextDetectorPlugin()

def execute(inputs):
    return plugin.execute(inputs)


