"""
Service pour récupérer les logs des géocaches depuis Geocaching.com.

Ce service utilise l'API interne de Geocaching.com pour récupérer les logs
d'une géocache. Il nécessite une authentification via les cookies du navigateur.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests

from .geocaching_auth import get_auth_service

logger = logging.getLogger(__name__)


@dataclass
class GeocacheLogData:
    """Représente un log récupéré depuis Geocaching.com."""
    external_id: str
    author: str
    author_guid: str | None
    text: str
    date: datetime | None
    log_type: str
    is_favorite: bool


class GeocachingLogsClient:
    """
    Client pour récupérer les logs des géocaches depuis Geocaching.com.
    
    Utilise l'API interne de Geocaching.com qui retourne les logs en JSON.
    L'authentification se fait via les cookies du navigateur (Firefox, Chrome, Edge).
    
    Stratégie:
    1. Récupérer la page HTML de la géocache
    2. Extraire le userToken de la page
    3. Utiliser ce token pour appeler l'API des logs
    """
    
    # URL de la page de la géocache (pour extraire le userToken)
    GEOCACHE_PAGE_URL = 'https://www.geocaching.com/geocache/{gc_code}'
    
    # URL de l'API des logs (nécessite le userToken)
    LOGS_API_URL = 'https://www.geocaching.com/seek/geocache.logbook'
    
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        # Utiliser la session du service d'authentification centralisé
        if session is not None:
            self.session = session
        else:
            auth_service = get_auth_service()
            self.session = auth_service.get_session()
        
        self.session.headers.setdefault('User-Agent', 'GeoApp/1.0 (+https://example.local)')
        self.session.headers.setdefault('Accept', 'application/json')
        self.session.headers.setdefault('X-Requested-With', 'XMLHttpRequest')
    
    def get_logs(
        self, 
        gc_code: str, 
        count: int = 25, 
        log_type: str = 'all'
    ) -> list[GeocacheLogData]:
        """
        Récupère les logs d'une géocache depuis Geocaching.com.
        
        Stratégie:
        1. Récupérer la page HTML de la géocache
        2. Extraire le userToken de la page
        3. Utiliser ce token pour appeler l'API des logs
        
        Args:
            gc_code: Code GC de la géocache (ex: GC12345)
            count: Nombre de logs à récupérer (défaut: 25)
            log_type: Type de logs à récupérer ('all', 'friends', 'own')
            
        Returns:
            Liste des logs récupérés
            
        Raises:
            LookupError: Si la géocache n'est pas trouvée
            requests.RequestException: En cas d'erreur réseau
        """
        gc_code = gc_code.strip().upper()
        logger.info(f"Fetching logs for {gc_code} (count={count}, type={log_type})")
        
        # Étape 1: Récupérer le userToken depuis la page de la géocache
        user_token = self._get_user_token(gc_code)
        if not user_token:
            logger.error(f"Could not get userToken for {gc_code}")
            return []
        
        # Étape 2: Appeler l'API des logs avec le token
        return self._fetch_logs_with_token(user_token, count, log_type)
    
    def _get_user_token(self, gc_code: str) -> str | None:
        """
        Récupère le userToken depuis la page HTML de la géocache.
        
        Le userToken est un token chiffré nécessaire pour appeler l'API des logs.
        Il est présent dans le JavaScript de la page sous la forme:
        userToken = 'XXXXX...'
        
        Args:
            gc_code: Code GC de la géocache
            
        Returns:
            Le userToken ou None si non trouvé
        """
        url = self.GEOCACHE_PAGE_URL.format(gc_code=gc_code)
        logger.debug(f"Fetching geocache page to extract userToken: {url}")
        
        try:
            # Utiliser les headers pour une requête HTML normale
            headers = {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            }
            resp = self.session.get(url, headers=headers, timeout=30)
            
            if resp.status_code == 404:
                logger.warning(f"Geocache {gc_code} not found (404)")
                raise LookupError('gc_not_found')
            
            resp.raise_for_status()
            
            # Chercher le userToken dans la page
            # Format: userToken = 'XXXXX...'
            token_match = re.search(r"userToken\s*=\s*'([^']+)'", resp.text)
            if token_match:
                token = token_match.group(1)
                logger.debug(f"Found userToken for {gc_code}: {token[:30]}...")
                return token
            
            logger.warning(f"userToken not found in page for {gc_code}")
            return None
            
        except requests.RequestException as e:
            logger.error(f"Failed to fetch geocache page for {gc_code}: {e}")
            raise
    
    def _fetch_logs_with_token(
        self, 
        user_token: str, 
        count: int, 
        log_type: str
    ) -> list[GeocacheLogData]:
        """
        Récupère les logs via l'API en utilisant le userToken.
        
        Args:
            user_token: Token chiffré extrait de la page
            count: Nombre de logs à récupérer
            log_type: Type de logs ('all', 'friends', 'own')
            
        Returns:
            Liste des logs récupérés
        """
        params = {
            'tkn': user_token,
            'idx': 1,
            'num': count,
            'decrypt': 'false',
        }
        
        # Ajouter le filtre de type si nécessaire
        if log_type.lower() == 'friends':
            params['sf'] = 'true'
        elif log_type.lower() == 'own':
            params['sp'] = 'true'
        
        try:
            # Headers pour une requête AJAX
            headers = {
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'X-Requested-With': 'XMLHttpRequest',
            }
            
            logger.debug(f"Requesting logs API with token: {user_token[:30]}...")
            resp = self.session.get(self.LOGS_API_URL, params=params, headers=headers, timeout=30)
            
            resp.raise_for_status()
            
            # Parser la réponse JSON
            data = resp.json()
            
            # Vérifier le statut de la réponse
            if isinstance(data, dict):
                status = data.get('status', '')
                if status == 'error':
                    error_msg = data.get('msg', 'Unknown error')
                    logger.error(f"Logs API returned error: {error_msg}")
                    return []
                
                if status == 'success' and 'data' in data:
                    logs = self._parse_legacy_logs(data['data'])
                    logger.info(f"Retrieved {len(logs)} logs")
                    return logs
            
            logger.warning(f"Unexpected response format from logs API")
            return []
            
        except requests.exceptions.JSONDecodeError as e:
            logger.error(f"Failed to parse logs JSON: {e}")
            return []
        except requests.RequestException as e:
            logger.error(f"Failed to fetch logs: {e}")
            return []
    
    def _parse_legacy_logs(self, logs_data: list) -> list[GeocacheLogData]:
        """
        Parse les logs depuis l'API legacy.
        
        Format attendu:
        {
            "LogID": 1336648432,
            "LogGuid": "0e47266d-0c68-4956-ab59-3f31305641b7",
            "LogType": "Found it",
            "LogText": "<p>...</p>",
            "Created": "12/02/2025",
            "Visited": "11/30/2025",
            "UserName": "geokaboutervinnie",
            "AccountGuid": "602bdfc3-c48e-4a14-8449-216dfc1416fb",
            "FavoritePointUsed": false
        }
        """
        logs = []
        
        for entry in logs_data:
            try:
                external_id = str(entry.get('LogID', entry.get('LogGuid', '')))
                author = entry.get('UserName', 'Unknown')
                author_guid = entry.get('AccountGuid')
                
                text = entry.get('LogText', '')
                text = self._clean_log_text(text)
                
                # Utiliser Visited (date de visite) plutôt que Created (date de création du log)
                date_str = entry.get('Visited', entry.get('Created', ''))
                date = self._parse_date(date_str)
                
                log_type = entry.get('LogType', 'Unknown')
                is_favorite = bool(entry.get('FavoritePointUsed', False))
                
                logs.append(GeocacheLogData(
                    external_id=external_id,
                    author=author,
                    author_guid=author_guid,
                    text=text,
                    date=date,
                    log_type=log_type,
                    is_favorite=is_favorite,
                ))
            except Exception as e:
                logger.warning(f"Failed to parse log entry: {e}")
                continue
        
        return logs
    
    def _clean_log_text(self, text: str) -> str:
        """
        Nettoie le texte d'un log (supprime le HTML basique).
        
        Args:
            text: Texte brut ou HTML
            
        Returns:
            Texte nettoyé
        """
        if not text:
            return ''
        
        # Remplacer les balises <br> par des sauts de ligne
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        
        # Supprimer les autres balises HTML
        text = re.sub(r'<[^>]+>', '', text)
        
        # Décoder les entités HTML courantes
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        
        # Nettoyer les espaces multiples
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = text.strip()
        
        return text
    
    def _parse_date(self, date_str: str) -> datetime | None:
        """
        Parse une date depuis différents formats possibles.
        
        Args:
            date_str: Chaîne de date
            
        Returns:
            datetime ou None si le parsing échoue
        """
        if not date_str:
            return None
        
        # Formats de date possibles
        formats = [
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%d',
            '%m/%d/%Y',
            '%d/%m/%Y',
        ]
        
        # Nettoyer la chaîne (enlever les millisecondes et timezone)
        date_str = re.sub(r'\.\d+', '', date_str)
        date_str = re.sub(r'[+-]\d{2}:\d{2}$', '', date_str)
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        
        logger.warning(f"Could not parse date: {date_str}")
        return None
