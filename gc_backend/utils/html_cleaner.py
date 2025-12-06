"""Utilitaire pour nettoyer le HTML."""

import re
from html import unescape
from typing import Optional


def html_to_text_with_linebreaks(html: Optional[str]) -> Optional[str]:
    """Convertit du HTML en texte brut en préservant les sauts de ligne."""
    if html is None:
        return None
    if not html.strip():
        return ""
    
    text = html
    # Convertir les balises <br> en sauts de ligne
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    # Convertir les balises de bloc en sauts de ligne
    for tag in ['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'blockquote', 'pre']:
        text = re.sub(rf'</{tag}\s*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(rf'<{tag}[^>]*>', '\n', text, flags=re.IGNORECASE)
    # Supprimer toutes les autres balises HTML
    text = re.sub(r'<[^>]+>', '', text)
    # Décoder les entités HTML
    text = unescape(text)
    # Nettoyer
    text = text.strip()
    return text


def clean_html_description(html: Optional[str]) -> Optional[str]:
    """Alias pour html_to_text_with_linebreaks."""
    return html_to_text_with_linebreaks(html)
