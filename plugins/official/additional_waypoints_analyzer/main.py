from typing import Dict, Any, List
from loguru import logger
from bs4 import BeautifulSoup

class AdditionalWaypointsAnalyzerPlugin:
    def __init__(self):
        self.name = "additional_waypoints_analyzer"
        self.description = "Détecte les waypoints additionnels"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        html_content = inputs.get('text', '')
        waypoints_input = inputs.get('waypoints')
        
        results = []
        
        # 1. Analyse des waypoints passés explicitement (via l'API/Frontend)
        if waypoints_input and isinstance(waypoints_input, list):
            for i, wp in enumerate(waypoints_input):
                # On s'intéresse particulièrement aux waypoints finaux ou physiques
                wp_type = wp.get('type', '').lower()
                wp_name = wp.get('name', '')
                wp_coords = wp.get('gc_coords') or wp.get('coordinates_raw')
                
                # Si on a des coordonnées, c'est un résultat intéressant
                if wp_coords:
                    confidence = 1.0 if 'final' in wp_type or 'final' in wp_name.lower() else 0.8
                    
                    results.append({
                        "id": f"explicit_waypoint_{i}",
                        "text_output": f"Waypoint ({wp.get('prefix', '')}): {wp_name} - {wp_coords} [{wp.get('type', '')}]",
                        "coordinates": {
                            "formatted": wp_coords,
                            # Ajouter lat/lon si dispo pour que l'aggrégateur puisse les utiliser
                            "latitude": wp.get('latitude'), 
                            "longitude": wp.get('longitude')
                        },
                        "decimal_latitude": wp.get('latitude'),
                        "decimal_longitude": wp.get('longitude'),
                        "confidence": confidence,
                        "metadata": {"source": "waypoints_list", "type": wp.get('type')}
                    })

        # 2. Analyse du HTML (fallback ou complément)
        if html_content:
            try:
                soup = BeautifulSoup(html_content, 'html.parser')
                
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
                                    "id": f"html_table_row_{idx}_{r_idx}",
                                    "text_output": f"Waypoint potentiel (HTML): {line_content}",
                                    "raw_data": col_texts,
                                    "confidence": 0.5
                                })
            except Exception as e:
                logger.warning(f"Erreur parsing HTML dans waypoints analyzer: {e}")

        summary_msg = f"{len(results)} waypoints analysés."
        
        # Si on a trouvé un "Final", on le met en avant
        final_wps = [r for r in results if r.get('confidence', 0) >= 0.9]
        if final_wps:
            summary_msg += " Waypoint final identifié !"

        return {
            "status": "success",
            "summary": summary_msg,
            "results": results
        }

plugin = AdditionalWaypointsAnalyzerPlugin()

def execute(inputs):
    return plugin.execute(inputs)

