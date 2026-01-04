"""
Service d'Extraction de Questions pour Formula Solver

Ce service extrait les questions associées aux variables (lettres) dans les formules
de coordonnées GPS à partir de descriptions de géocaches.

Supporte deux méthodes :
- Regex (par défaut) : Recherche de patterns structurés dans le texte
- AI (future) : Analyse sémantique avec IA (non implémenté pour l'instant)
"""

import re
from typing import List, Dict, Any, Union
from loguru import logger


class FormulaQuestionsService:
    """Service pour extraire les questions associées aux variables dans les formules de coordonnées"""
    
    def __init__(self):
        """Initialise le service d'extraction de questions"""
        logger.info("FormulaQuestionsService initialisé")
    
    def extract_questions_with_regex(
        self,
        content: Union[str, Any],
        letters: List[str]
    ) -> Dict[str, str]:
        """
        Extrait les questions associées aux lettres spécifiées en utilisant des patterns regex.
        
        Supporte plusieurs formats de questions :
        - Format simple : "A. Question ici"
        - Format avec double-points : "B: Question ici"
        - Format avec parenthèses : "C) Question ici"
        - Format inverse : "Question A:"
        - Format numéroté : "1. (D) Question ici"
        
        Args:
            content: Contenu textuel ou objet Geocache à analyser
            letters: Liste des lettres à rechercher (ex: ['A', 'B', 'C'])
        
        Returns:
            Dict[str, str]: Dictionnaire associant chaque lettre à sa question
                           Exemple: {'A': 'Nombre de fenêtres', 'B': 'Année de construction'}
        """
        logger.debug(f"Extraction de questions pour les lettres: {letters}")
        
        # Préparer le contenu
        prepared_content = self._prepare_content_for_analysis(content)
        
        if not prepared_content:
            logger.warning("Contenu vide après préparation")
            return {letter: "" for letter in letters}
        
        logger.debug(f"Contenu préparé ({len(prepared_content)} caractères)")
        logger.debug(f"Contenu préparé (aperçu): {repr(prepared_content[:200])}...")

        # Vérifier si le contenu contient encore du HTML
        if '<' in prepared_content and '>' in prepared_content:
            logger.warning("⚠️ ATTENTION: Le contenu contient encore des balises HTML ! Le nettoyage a échoué.")
        else:
            logger.debug("✅ Contenu nettoyé correctement (pas de balises HTML)")
        
        # Initialiser le résultat
        result = {letter: "" for letter in letters}
        
        # Créer le pattern pour les lettres recherchées
        letters_pattern = '|'.join(re.escape(letter) for letter in letters)
        
        # Définir les séparateurs possibles
        # Point, double-points, parenthèse fermante, tiret, tiret long, tiret cadratin, slash
        separators_class = r'[.:\)\-–—/]'  # NB: '=' est traité par un pattern dédié ci-dessous
        
        # Patterns regex pour différents formats de questions
        patterns = [
            # Format très courant: "A = instruction..." (ou "A=...")
            # Exemple: "A = valeur du nom complet (en 4 mots) (A=1…Z=26)"
            rf'(?:^|\n)\s*({letters_pattern})\s*=\s*([^\n]+)',

            # Format: A. / A: / A) / A- suivi du texte jusqu'au prochain en-tête ou fin
            # Exemple: "A. Combien de fenêtres?"
            rf'(?:^|\n)\s*({letters_pattern})\s*{separators_class}\s*(.*?)(?=\n\s*[A-Z]\s*{separators_class}|\n\s*\d+\s*{separators_class}|$)',
            
            # Format: 1. (A) Question?
            # Exemple: "1. (A) Combien de fenêtres?"
            rf'(?:^|\n)\s*\d+\s*{separators_class}\s*\(({letters_pattern})\)\s*(.*?)(?=\n\s*\d+\s*{separators_class}|\n\s*[A-Z]\s*{separators_class}|$)',

            # Format: Question ... (A) ?  (la lettre en fin de ligne, entre parenthèses)
            # Exemple: "Coté artistique ... (A) ?"
            # Note: On limite volontairement à une seule ligne pour éviter les faux positifs.
            rf'(?:^|\n)\s*([^\n]{5,200}?)\s*\(\s*({letters_pattern})\s*\)\s*[?!\.…]*\s*(?=\n|$)',
            
            # Format: Question A:  (la lettre après le texte) - DERNIÈRE PRIORITÉ
            # Exemple: "Nombre de fenêtres A:"
            # Note: Ce pattern doit être le dernier car il peut capturer de faux positifs
            rf'(?:^|\n)\s*([^:\n]{{5,50}}?)\s+({letters_pattern})\s*{separators_class}\s*$',
        ]
        
        # Parcourir tous les patterns
        for pattern_idx, pattern in enumerate(patterns):
            logger.debug(f"Traitement pattern {pattern_idx + 1}: {pattern}")
            matches = re.finditer(pattern, prepared_content, re.DOTALL | re.MULTILINE)
            matches_list = list(matches)  # Convertir en liste pour pouvoir compter et déboguer
            logger.debug(f"Pattern {pattern_idx + 1}: {len(matches_list)} matches trouvés")
            
            for match in matches_list:
                logger.debug(f"Match trouvé: groupes={match.groups()}, span={match.span()}")
                if len(match.groups()) >= 2:
                    # Déterminer quel groupe contient la lettre, de manière robuste
                    # (évite de dépendre de l'ordre des patterns).
                    group1 = (match.group(1) or '').strip()
                    group2 = (match.group(2) or '').strip()

                    letter = ''
                    question = ''

                    if group1.upper() in letters:
                        letter = group1.upper()
                        question = group2
                    elif group2.upper() in letters:
                        letter = group2.upper()
                        question = group1
                    else:
                        continue
                    
                    # Vérifier que la lettre est bien dans notre liste
                    if letter in letters:
                        logger.debug(f"Lettre {letter} trouvée dans la liste, question: '{question[:100]}...'")
                        # Ne remplacer que si la question n'est pas déjà trouvée
                        # ou si celle-ci est plus longue ET que c'est le même pattern
                        if not result[letter]:
                            result[letter] = question
                            logger.debug(f"Question trouvée pour {letter}: {question[:50]}...")
                        elif len(question) > len(result[letter]) and pattern_idx < 3:
                            # Seulement remplacer si plus long et pas les formats fin-de-ligne
                            result[letter] = question
                            logger.debug(f"Question mise à jour pour {letter}: {question[:50]}...")
                    else:
                        logger.debug(f"Lettre {letter} ignorée (pas dans la liste recherchée)")
        
        # Compter les questions trouvées
        found_count = len([q for q in result.values() if q])
        logger.info(f"Extraction regex terminée: {found_count}/{len(letters)} questions trouvées")
        
        return result
    
    def _prepare_content_for_analysis(
        self,
        content: Union[str, Any]
    ) -> str:
        """
        Prépare le contenu pour l'analyse en extrayant les parties pertinentes.
        
        Si le contenu est une chaîne : retourne telle quelle
        Si le contenu est un objet Geocache : extrait description + waypoints + hints
        
        Args:
            content: Contenu brut (str) ou objet Geocache
        
        Returns:
            str: Contenu préparé et nettoyé pour l'analyse
        """
        # Cas 1 : Contenu déjà sous forme de chaîne
        if isinstance(content, str):
            # Nettoyer le HTML même pour les chaînes brutes
            logger.debug("Contenu reçu sous forme de chaîne - nettoyage HTML appliqué")
            return self._clean_html(content)
        
        # Cas 2 : Objet Geocache (avec description et données enrichies)
        if hasattr(content, 'id') and (hasattr(content, 'description') or hasattr(content, 'description_html')):
            geocache = content
            content_parts = []

            # Ajouter la description principale (utiliser description_raw si disponible)
            description = getattr(geocache, 'description_raw', None)
            if not description:
                # Fallback vers description_html si description_raw n'existe pas
                description = getattr(geocache, 'description_html', None)
                if description:
                    description = self._clean_html(description)

            # Fallback final vers description
            if not description:
                description = getattr(geocache, 'description', None)

            if description:
                content_parts.append("=== DESCRIPTION PRINCIPALE ===\n")
                content_parts.append(description)
                content_parts.append("\n\n")

            # Ajouter les waypoints additionnels (anciens ou nouveaux schémas)
            waypoints = None
            if hasattr(geocache, 'additional_waypoints') and geocache.additional_waypoints:
                waypoints = geocache.additional_waypoints
            elif hasattr(geocache, 'waypoints') and geocache.waypoints:
                waypoints = geocache.waypoints

            if waypoints:
                content_parts.append("=== WAYPOINTS ADDITIONNELS ===\n")
                for wp in waypoints:
                    prefix = getattr(wp, 'prefix', '') or ''
                    name = getattr(wp, 'name', '') or ''
                    if prefix or name:
                        content_parts.append(f"{prefix} {name}\n".strip() + "\n")

                    note = getattr(wp, 'note', '')
                    if note:
                        cleaned_note = self._clean_html(note)
                        content_parts.append(f"Note: {cleaned_note}\n")

                    content_parts.append("\n")

            # Ajouter les hints / indices
            hint = (
                getattr(geocache, 'hints_decoded', None)
                or getattr(geocache, 'hint', None)
                or getattr(geocache, 'hints', None)
            )
            if hint:
                content_parts.append("=== INDICE ===\n")
                cleaned_hint = self._clean_html(hint)
                content_parts.append(cleaned_hint)
                content_parts.append("\n\n")

            # Limiter la longueur si nécessaire (éviter de surcharger)
            full_content = "".join(content_parts)
            max_length = 10000  # 10K caractères max
            
            if len(full_content) > max_length:
                logger.warning(f"Contenu tronqué: {len(full_content)} -> {max_length} caractères")
                return full_content[:max_length] + "\n[...TRONQUÉ...]"
            
            return full_content
        
        # Cas 3 : Type non reconnu
        logger.warning(f"Type de contenu non reconnu: {type(content)}")
        return ""
    
    def _clean_html(self, html_content: str) -> str:
        """
        Nettoie le contenu HTML pour en extraire le texte brut.
        
        Utilise BeautifulSoup si disponible, sinon utilise une regex simple.
        
        Args:
            html_content: Contenu HTML à nettoyer
        
        Returns:
            str: Texte nettoyé sans balises HTML
        """
        if not html_content:
            return ""
        
        try:
            # Méthode 1 : BeautifulSoup (préféré)
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remplacer les éléments de bloc par leur contenu + saut de ligne
            # pour que les patterns regex puissent détecter les questions séparément
            for tag in soup.find_all(['li', 'p', 'div', 'br', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                # Insérer un saut de ligne après le tag
                tag.insert_after('\n')
            
            # Extraire le texte
            text = soup.get_text(separator=' ')
            
            # Nettoyer les espaces multiples SAUF les sauts de ligne
            text = re.sub(r'[ \t]+', ' ', text)  # Espaces horizontaux seulement
            text = re.sub(r'\n[ \t]+', '\n', text)  # Espaces après saut de ligne
            text = re.sub(r'[ \t]+\n', '\n', text)  # Espaces avant saut de ligne
            text = re.sub(r'\n{3,}', '\n\n', text)  # Max 2 sauts de ligne consécutifs
            
            return text.strip()
            
        except ImportError:
            # Méthode 2 : Regex simple (fallback)
            logger.warning("BeautifulSoup non disponible, utilisation de regex simple")
            
            # Supprimer toutes les balises HTML
            clean = re.compile('<.*?>')
            text = re.sub(clean, ' ', html_content)
            
            # Nettoyer les espaces multiples
            text = re.sub(r'\s+', ' ', text)
            
            # Décoder les entités HTML communes
            text = text.replace('&nbsp;', ' ')
            text = text.replace('&lt;', '<')
            text = text.replace('&gt;', '>')
            text = text.replace('&amp;', '&')
            text = text.replace('&quot;', '"')
            
            return text.strip()
        
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage HTML: {e}")
            # En cas d'erreur, au moins retirer les balises avec regex
            clean = re.compile('<.*?>')
            return re.sub(clean, ' ', html_content)
    
    def extract_questions_with_ai(
        self,
        content: Union[str, Any],
        letters: List[str]
    ) -> Dict[str, str]:
        """
        Extrait les questions associées aux lettres en utilisant l'IA.
        
        NOTE: Cette méthode n'est pas encore implémentée.
        Elle sera ajoutée dans une phase ultérieure du projet.
        
        Args:
            content: Contenu à analyser
            letters: Liste des lettres à rechercher
        
        Returns:
            Dict[str, str]: Dictionnaire vide pour l'instant
        
        Raises:
            NotImplementedError: Cette fonctionnalité n'est pas encore disponible
        """
        logger.warning("La méthode AI n'est pas encore implémentée")
        raise NotImplementedError(
            "L'extraction de questions avec IA sera implémentée dans une phase future. "
            "Utilisez extract_questions_with_regex() pour l'instant."
        )


# Instance singleton du service
formula_questions_service = FormulaQuestionsService()


# Export pour utilisation externe
__all__ = ['FormulaQuestionsService', 'formula_questions_service']
