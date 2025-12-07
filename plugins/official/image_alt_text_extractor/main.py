from typing import Dict, Any, List
from loguru import logger
from bs4 import BeautifulSoup

class ImageAltTextExtractorPlugin:
    def __init__(self):
        self.name = "image_alt_text_extractor"
        self.description = "Extrait attributs alt et title des images"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        html_content = inputs.get('text', '')
        
        if not html_content:
            return {"status": "success", "results": [], "summary": "Aucun texte"}

        try:
            soup = BeautifulSoup(html_content, 'html.parser')
        except Exception as e:
            return {"status": "error", "summary": f"Erreur parsing HTML: {e}", "results": []}

        results = []
        
        for idx, img in enumerate(soup.find_all('img')):
            alt = img.get('alt', '').strip()
            title = img.get('title', '').strip()
            src = img.get('src', '')
            
            if alt or title:
                info_text = ""
                if alt:
                    info_text += f"Alt: '{alt}' "
                if title:
                    info_text += f"Title: '{title}'"
                
                results.append({
                    "id": f"img_text_{idx}",
                    "text_output": f"Image {idx+1}: {info_text}",
                    "alt_text": alt,
                    "title_text": title,
                    "src": src,
                    "confidence": 1.0
                })

        return {
            "status": "success",
            "summary": f"{len(results)} textes d'images trouvés",
            "results": results
        }

plugin = ImageAltTextExtractorPlugin()

def execute(inputs):
    return plugin.execute(inputs)


