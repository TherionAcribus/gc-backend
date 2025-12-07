from typing import Dict, Any, List
from loguru import logger
from bs4 import BeautifulSoup, Comment

class HtmlCommentsFinderPlugin:
    def __init__(self):
        self.name = "html_comments_finder"
        self.description = "Extrait les commentaires HTML"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        html_content = inputs.get('text', '')
        
        if not html_content:
            return {"status": "success", "results": [], "summary": "Aucun texte"}

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            return {"status": "error", "summary": f"Erreur parsing HTML: {e}", "results": []}

        results = []
        
        comments = soup.find_all(string=lambda text: isinstance(text, Comment))
        
        for idx, comment in enumerate(comments):
            clean_comment = comment.strip()
            if clean_comment:
                # Filtrer les commentaires triviaux si nécessaire (ex: doctype, ou commentaires vides)
                results.append({
                    "id": f"comment_{idx}",
                    "text_output": f"Commentaire HTML : '{clean_comment}'",
                    "comment_content": clean_comment,
                    "confidence": 1.0
                })

        return {
            "status": "success",
            "summary": f"{len(results)} commentaires trouvés",
            "results": results
        }

plugin = HtmlCommentsFinderPlugin()

def execute(inputs):
    return plugin.execute(inputs)


