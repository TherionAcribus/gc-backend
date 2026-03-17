"""
Service pour envoyer des coordonnées corrigées vers Geocaching.com.

Utilise la même approche que c:geo (GCParser.editModifiedCoordinates) :
  - Extraction du userToken depuis la page HTML de la géocache
  - POST vers cache_details.aspx/SetUserCoordinate  (mise à jour)
  - POST vers cache_details.aspx/ResetUserCoordinate (suppression)

Référence c:geo :
  https://github.com/cgeo/cgeo/blob/master/main/src/main/java/cgeo/geocaching/connector/gc/GCParser.java
  Pattern userToken : PATTERN_USERTOKEN = Pattern.compile("userToken\\s*=\\s*'([^']+)'")
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

import requests

from .geocaching_auth import get_auth_service

logger = logging.getLogger(__name__)

_PATTERN_USERTOKEN = re.compile(r"userToken\s*=\s*'([^']+)'")

_BASE_URL = 'https://www.geocaching.com'
_SET_COORD_URL = f'{_BASE_URL}/seek/cache_details.aspx/SetUserCoordinate'
_RESET_COORD_URL = f'{_BASE_URL}/seek/cache_details.aspx/ResetUserCoordinate'


class GeocachingPushCoordinatesClient:
    """Client pour envoyer/supprimer des coordonnées corrigées sur Geocaching.com.

    Identique à l'approche c:geo (GCParser.editModifiedCoordinates).
    """

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        if session is not None:
            self.session = session
        else:
            auth_service = get_auth_service()
            self.session = auth_service.get_session()

        self.session.headers.setdefault(
            'User-Agent',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        )

    def get_user_token(self, gc_code: str) -> str | None:
        """Extrait le userToken depuis la page HTML de la géocache.

        c:geo utilise PATTERN_USERTOKEN = Pattern.compile("userToken\\s*=\\s*'([^']+)'")
        Le token est embarqué dans le JS de la page geocache.
        """
        url = f'{_BASE_URL}/geocache/{gc_code}'
        try:
            resp = self.session.get(
                url,
                headers={'Accept': 'text/html,application/xhtml+xml'},
                timeout=30,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                logger.error('Failed to load geocache page for %s: status=%s', gc_code, resp.status_code)
                return None
            match = _PATTERN_USERTOKEN.search(resp.text)
            if not match:
                logger.error('userToken not found in geocache page for %s (page length=%d)', gc_code, len(resp.text))
                return None
            token = match.group(1)
            logger.debug('Got userToken for %s: %s...', gc_code, token[:8])
            return token
        except requests.RequestException as e:
            logger.error('Failed to get geocache page for %s: %s', gc_code, e)
            return None

    def push_corrected_coordinates(
        self,
        gc_code: str,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any]:
        """Envoie des coordonnées corrigées vers Geocaching.com.

        Utilise la même méthode que c:geo :
          POST cache_details.aspx/SetUserCoordinate
          body: {"dto": {"ut": "<userToken>", "data": {"lat": <lat>, "lng": <lng>}}}

        Args:
            gc_code: Code GC de la géocache (ex: "GC12345").
            latitude: Latitude en décimal (ex: 48.123456).
            longitude: Longitude en décimal (ex: 2.345678).

        Returns:
            dict avec 'ok' (bool) et éventuellement 'error'.
        """
        gc_code = gc_code.strip().upper()
        if not gc_code:
            return {'ok': False, 'error': 'Code GC manquant'}

        user_token = self.get_user_token(gc_code)
        if not user_token:
            return {'ok': False, 'error': f'Impossible d\'obtenir le userToken pour {gc_code} — vérifiez la connexion à Geocaching.com'}

        payload = {
            'dto': {
                'ut': user_token,
                'data': {
                    'lat': round(float(latitude), 8),
                    'lng': round(float(longitude), 8),
                },
            }
        }
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Referer': f'{_BASE_URL}/geocache/{gc_code}',
            'X-Requested-With': 'XMLHttpRequest',
        }

        try:
            resp = self.session.post(
                _SET_COORD_URL, json=payload, headers=headers, timeout=30, allow_redirects=False,
            )
            if resp.status_code == 302:
                location = resp.headers.get('Location', '')
                logger.error('SetUserCoordinate redirected for %s → %s', gc_code, location)
                return {'ok': False, 'error': f'Requête rejetée par Geocaching.com (redirection → {location})'}
            if resp.status_code not in (200, 201, 204):
                body_preview = (resp.text or '')[:500]
                logger.error(
                    'SetUserCoordinate failed for %s: status=%s body=%r',
                    gc_code, resp.status_code, body_preview,
                )
                return {'ok': False, 'status': resp.status_code, 'error': body_preview}
            logger.info('Corrected coordinates pushed successfully for %s', gc_code)
            return {'ok': True}
        except requests.RequestException as e:
            logger.error('Failed to push corrected coordinates for %s: %s', gc_code, e)
            return {'ok': False, 'error': str(e)}

    def delete_corrected_coordinates(self, gc_code: str) -> dict[str, Any]:
        """Supprime les coordonnées corrigées sur Geocaching.com.

        Utilise la même méthode que c:geo :
          POST cache_details.aspx/ResetUserCoordinate
          body: {"dto": {"ut": "<userToken>"}}

        Args:
            gc_code: Code GC de la géocache.

        Returns:
            dict avec 'ok' (bool) et éventuellement 'error'.
        """
        gc_code = gc_code.strip().upper()
        if not gc_code:
            return {'ok': False, 'error': 'Code GC manquant'}

        user_token = self.get_user_token(gc_code)
        if not user_token:
            return {'ok': False, 'error': f'Impossible d\'obtenir le userToken pour {gc_code} — vérifiez la connexion à Geocaching.com'}

        payload = {'dto': {'ut': user_token}}
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'Referer': f'{_BASE_URL}/geocache/{gc_code}',
            'X-Requested-With': 'XMLHttpRequest',
        }

        try:
            resp = self.session.post(
                _RESET_COORD_URL, json=payload, headers=headers, timeout=30, allow_redirects=False,
            )
            if resp.status_code == 302:
                location = resp.headers.get('Location', '')
                logger.error('ResetUserCoordinate redirected for %s → %s', gc_code, location)
                return {'ok': False, 'error': f'Requête rejetée par Geocaching.com (redirection → {location})'}
            if resp.status_code not in (200, 204):
                body_preview = (resp.text or '')[:500]
                logger.error(
                    'ResetUserCoordinate failed for %s: status=%s body=%r',
                    gc_code, resp.status_code, body_preview,
                )
                return {'ok': False, 'status': resp.status_code, 'error': body_preview}
            logger.info('Corrected coordinates deleted successfully for %s', gc_code)
            return {'ok': True}
        except requests.RequestException as e:
            logger.error('Failed to delete corrected coordinates for %s: %s', gc_code, e)
            return {'ok': False, 'error': str(e)}
