from __future__ import annotations

import logging
from datetime import date as date_type
from datetime import datetime, time, timezone
from typing import Any, Optional

import requests

from .geocaching_auth import get_auth_service

logger = logging.getLogger(__name__)


class GeocachingSubmitLogsClient:
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        # Utiliser la session du service d'authentification centralisé
        if session is not None:
            self.session = session
        else:
            auth_service = get_auth_service()
            self.session = auth_service.get_session()
        
        self.session.headers.setdefault('User-Agent', 'GeoApp/1.0 (+https://example.local)')
        self.session.headers.setdefault('Accept', 'application/json')

    def get_csrf_token(self) -> str | None:
        url = 'https://www.geocaching.com/api/auth/csrf'
        headers = {
            'Accept': 'application/json',
        }

        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                logger.error('CSRF token request failed: status=%s', resp.status_code)
                return None
            data = resp.json() if resp.content else None
            token = data.get('csrfToken') if isinstance(data, dict) else None
            return token if isinstance(token, str) and token.strip() else None
        except requests.RequestException as e:  # pragma: no cover
            logger.error('Failed to get CSRF token: %s', e)
            return None

    def upload_log_draft_image(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> dict[str, Any] | None:
        csrf_token = self.get_csrf_token()
        if not csrf_token:
            logger.error('Could not get CSRF token')
            return None

        url = 'https://www.geocaching.com/api/live/v1/logdrafts/images'
        headers = {
            'Accept': 'application/json',
            'CSRF-Token': csrf_token,
        }

        files_variants = [
            {'file': (filename, content, content_type)},
            {'image': (filename, content, content_type)},
            {'imageFile': (filename, content, content_type)},
        ]

        last_error: dict[str, Any] | None = None
        for files in files_variants:
            try:
                resp = self.session.post(url, headers=headers, files=files, timeout=60)
                if resp.status_code not in (200, 201):
                    body_preview = (resp.text or '')[:2000]
                    last_error = {
                        'ok': False,
                        'status': resp.status_code,
                        'body': body_preview,
                    }
                    continue

                try:
                    data = resp.json() if resp.content else None
                except Exception as e:  # pragma: no cover
                    logger.error('Log image upload invalid JSON: %s body=%r', e, (resp.text or '')[:2000])
                    last_error = {
                        'ok': False,
                        'status': resp.status_code,
                        'body': (resp.text or '')[:2000],
                    }
                    continue

                if isinstance(data, dict):
                    data.setdefault('ok', True)
                return data if isinstance(data, dict) else {'ok': True, 'data': data}
            except requests.RequestException as e:  # pragma: no cover
                logger.error('Failed to upload log image: %s', e)
                last_error = {'ok': False, 'status': 0, 'error': str(e)}
                continue

        return last_error

    @staticmethod
    def extract_image_guid(payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ('imageGuid', 'ImageGuid', 'guid', 'Guid', 'image_guid', 'imageGUID'):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in payload.values():
                guid = GeocachingSubmitLogsClient.extract_image_guid(value)
                if guid:
                    return guid
        if isinstance(payload, list):
            for value in payload:
                guid = GeocachingSubmitLogsClient.extract_image_guid(value)
                if guid:
                    return guid
        return None

    def submit_geocache_log(
        self,
        gc_code: str,
        *,
        log_type_id: int,
        log_text: str,
        visited_date: date_type,
        images: list[str] | None = None,
        used_favorite_point: bool | None = None,
    ) -> dict[str, Any] | None:
        gc_code = gc_code.strip().upper()
        if not gc_code:
            return None

        csrf_token = self.get_csrf_token()
        if not csrf_token:
            logger.error('Could not get CSRF token')
            return None

        dt = datetime.combine(visited_date, time(12, 0, 0), tzinfo=timezone.utc)
        log_date = dt.isoformat(timespec='milliseconds').replace('+00:00', 'Z')

        safe_images: list[str] = []
        if isinstance(images, list):
            for value in images:
                if isinstance(value, str) and value.strip():
                    safe_images.append(value.strip())

        payload: dict[str, Any] = {
            'images': safe_images,
            'logDate': log_date,
            'logText': log_text,
            'logType': log_type_id,
            'trackables': [],
        }
        if used_favorite_point is not None:
            payload['usedFavoritePoint'] = bool(used_favorite_point)

        url = f'https://www.geocaching.com/api/live/v1/logs/{gc_code}/geocacheLog'
        headers = {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'CSRF-Token': csrf_token,
        }

        try:
            resp = self.session.post(url, json=payload, headers=headers, timeout=60)
            if resp.status_code != 200:
                body_preview = (resp.text or '')[:2000]
                logger.error('Log submit failed for %s: status=%s body=%r', gc_code, resp.status_code, body_preview)
                return {
                    'ok': False,
                    'status': resp.status_code,
                    'body': body_preview,
                }

            try:
                data = resp.json() if resp.content else None
            except Exception as e:  # pragma: no cover
                logger.error('Log submit invalid JSON for %s: %s body=%r', gc_code, e, (resp.text or '')[:2000])
                return {
                    'ok': False,
                    'status': resp.status_code,
                    'body': (resp.text or '')[:2000],
                }

            if not isinstance(data, dict):
                return {
                    'ok': False,
                    'status': resp.status_code,
                    'body': (resp.text or '')[:2000],
                }

            data.setdefault('ok', True)
            return data
        except requests.RequestException as e:  # pragma: no cover
            logger.error('Failed to submit log for %s: %s', gc_code, e)
            return None
