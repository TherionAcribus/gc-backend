from typing import Dict, Any, List
from loguru import logger
from bs4 import BeautifulSoup

class AdditionalWaypointsAnalyzerPlugin:
    def __init__(self):
        self.name = "additional_waypoints_analyzer"
        self.description = "Détecte les waypoints additionnels"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        html_content = inputs.get('text', '')
        
        if not html_content:
            return {"status": "success", "results": [], "summary": "Aucun texte"}

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            return {"status": "error", "summary": f"Erreur parsing HTML: {e}", "results": []}

        results = []
        
        # Recherche de tables contenant des mots clés de waypoints
        tables = soup.find_all('table')
        for idx, table in enumerate(tables):
            # Convertir en texte pour analyse rapide
            table_text = table.get_text().lower()
            if 'waypoint' in table_text or 'prefix' in table_text or 'lookup' in table_text:
                # C'est probablement une table de waypoints
                # On essaie d'extraire les lignes
                rows = table.find_all('tr')
                for r_idx, row in enumerate(rows):
                    cols = row.find_all(['td', 'th'])
                    col_texts = [c.get_text(strip=True) for c in cols]
                    
                    # Heuristique simple: si une ligne contient des coordonnées
                    line_content = " | ".join(col_texts)
                    
                    # On ignore les entêtes probables
                    if r_idx == 0 and ('prefix' in line_content.lower() or 'coordinate' in line_content.lower()):
                         continue
                         
                    if len(col_texts) >= 2:
                        results.append({
                            "id": f"waypoint_row_{idx}_{r_idx}",
                            "text_output": f"Waypoint potentiel: {line_content}",
                            "raw_data": col_texts,
                            "confidence": 0.7
                        })

        return {
            "status": "success",
            "summary": f"{len(results)} lignes de waypoints potentiels trouvées",
            "results": results
        }

plugin = AdditionalWaypointsAnalyzerPlugin()

def execute(inputs):
    return plugin.execute(inputs)

