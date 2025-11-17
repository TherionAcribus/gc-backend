"""
Service de recherche web pour le Formula Solver
Permet de rechercher des réponses sur Internet via DuckDuckGo
"""

import requests
from typing import List, Dict, Optional
from loguru import logger


class WebSearchService:
    """
    Service pour rechercher des informations sur Internet.
    Utilise l'API DuckDuckGo Instant Answer (pas besoin de clé API).
    """
    
    def __init__(self):
        self.ddg_api_url = "https://api.duckduckgo.com/"
        self.timeout = 10
        self.max_results = 5
    
    def search(
        self, 
        query: str, 
        context: Optional[str] = None,
        max_results: Optional[int] = None
    ) -> List[Dict[str, any]]:
        """
        Recherche une réponse sur Internet.
        
        Args:
            query: La question à rechercher
            context: Contexte additionnel pour affiner la recherche
            max_results: Nombre maximum de résultats (défaut: 5)
            
        Returns:
            Liste de résultats avec leur contenu et score de pertinence:
            [
                {
                    "text": "Réponse trouvée",
                    "source": "URL ou source",
                    "score": 0.85,
                    "type": "instant_answer" | "snippet"
                }
            ]
        """
        if max_results is None:
            max_results = self.max_results
        
        # Enrichir la requête avec le contexte
        search_query = f"{query} {context}" if context else query
        
        logger.info(f"Web search: {search_query}")
        
        results = []
        
        # Essayer DuckDuckGo Instant Answer API
        try:
            ddg_results = self._search_duckduckgo(search_query)
            results.extend(ddg_results)
        except Exception as e:
            logger.warning(f"Erreur recherche DuckDuckGo: {e}")
        
        # Limiter aux N meilleurs résultats
        results = results[:max_results]
        
        # Si pas de résultats, retourner un message informatif
        if not results:
            logger.warning(f"Aucun résultat trouvé pour: {search_query}")
            results.append({
                "text": "Aucun résultat trouvé sur Internet",
                "source": "web_search",
                "score": 0.0,
                "type": "no_result"
            })
        
        logger.info(f"Web search completed: {len(results)} résultats")
        
        return results
    
    def _search_duckduckgo(self, query: str) -> List[Dict[str, any]]:
        """
        Recherche via l'API DuckDuckGo Instant Answer.
        
        Args:
            query: Requête de recherche
            
        Returns:
            Liste de résultats
        """
        results = []
        
        try:
            params = {
                'q': query,
                'format': 'json',
                'no_html': 1,
                'skip_disambig': 1
            }
            
            response = requests.get(
                self.ddg_api_url,
                params=params,
                timeout=self.timeout
            )
            
            if response.status_code != 200:
                logger.warning(f"DuckDuckGo API returned status {response.status_code}")
                return results
            
            data = response.json()
            
            # Instant Answer (réponse directe)
            if data.get('Abstract'):
                results.append({
                    "text": data['Abstract'],
                    "source": data.get('AbstractURL', 'DuckDuckGo'),
                    "score": 0.9,
                    "type": "instant_answer"
                })
            
            # Answer (réponse courte)
            if data.get('Answer'):
                results.append({
                    "text": data['Answer'],
                    "source": 'DuckDuckGo Answer',
                    "score": 0.95,
                    "type": "instant_answer"
                })
            
            # Related Topics
            related_topics = data.get('RelatedTopics', [])
            for topic in related_topics[:3]:  # Limiter à 3 topics
                if isinstance(topic, dict) and topic.get('Text'):
                    results.append({
                        "text": topic['Text'],
                        "source": topic.get('FirstURL', 'DuckDuckGo'),
                        "score": 0.7,
                        "type": "snippet"
                    })
        
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout lors de la recherche DuckDuckGo")
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur réseau DuckDuckGo: {e}")
        except Exception as e:
            logger.error(f"Erreur inattendue DuckDuckGo: {e}")
        
        return results
    
    def extract_answer(self, results: List[Dict[str, any]]) -> Optional[str]:
        """
        Extrait la meilleure réponse depuis les résultats de recherche.
        
        Args:
            results: Liste de résultats de recherche
            
        Returns:
            La meilleure réponse trouvée, ou None
        """
        if not results:
            return None
        
        # Trier par score décroissant
        sorted_results = sorted(results, key=lambda x: x.get('score', 0), reverse=True)
        
        # Retourner le texte du meilleur résultat
        best_result = sorted_results[0]
        
        if best_result.get('type') == 'no_result':
            return None
        
        return best_result.get('text')


# Instance singleton
web_search_service = WebSearchService()

