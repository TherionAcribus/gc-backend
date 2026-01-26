from __future__ import annotations

import logging
import re
from typing import Optional

import requests

from .geocaching_auth import get_auth_service
from .geocaching_logs import GeocachingLogsClient

logger = logging.getLogger(__name__)


class GeocachingPersonalNotesClient:
    def __init__(self, session: Optional[requests.Session] = None) -> None:
        # Utiliser la session du service d'authentification centralisé
        if session is not None:
            self.session = session
        else:
            auth_service = get_auth_service()
            self.session = auth_service.get_session()
        
        self.session.headers.setdefault("User-Agent", "GeoApp/1.0 (+https://example.local)")

    def _get_user_token(self, gc_code: str) -> str | None:
        client = GeocachingLogsClient(session=self.session)
        return client._get_user_token(gc_code)  # type: ignore[attr-defined]

    def update_personal_note(self, gc_code: str, note: str) -> bool:
        gc_code = gc_code.strip().upper()
        if not gc_code:
            return False

        token = self._get_user_token(gc_code)
        if not token:
            logger.error("Could not get userToken for %s", gc_code)
            return False

        url = "https://www.geocaching.com/seek/cache_details.aspx/SetUserCacheNote"
        payload = {
            "dto": {
                "et": note,
                "ut": token,
            }
        }

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            logger.info("Updating personal note on Geocaching.com for %s", gc_code)
            resp = self.session.post(url, json=payload, headers=headers, timeout=30)
            logger.debug("SetUserCacheNote status=%s", resp.status_code)
            if resp.status_code != 200:
                logger.error("SetUserCacheNote failed for %s: status=%s", gc_code, resp.status_code)
                return False

            try:
                data = resp.json()
                logger.debug("SetUserCacheNote response JSON type=%s", type(data))
            except Exception:  # pragma: no cover
                data = None

            return True
        except requests.RequestException as e:  # pragma: no cover
            logger.error("Failed to update personal note for %s: %s", gc_code, e)
            return False

    def get_personal_note(self, gc_code: str) -> str | None:
        gc_code = gc_code.strip().upper()
        if not gc_code:
            return None

        url = f"https://www.geocaching.com/geocache/{gc_code}"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        try:
            logger.info("Fetching personal note from Geocaching.com page for %s", gc_code)
            resp = self.session.get(url, headers=headers, timeout=30)
            if resp.status_code == 404:
                logger.warning("Geocache %s not found (404) when fetching personal note", gc_code)
                return None
            resp.raise_for_status()

            html = resp.text or ""

            def _clean_note_text(raw: str) -> str:
                text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", "", text)
                text = text.replace("&nbsp;", " ")
                text = text.replace("&amp;", "&")
                text = text.replace("&lt;", "<")
                text = text.replace("&gt;", ">")
                text = text.replace("&quot;", '"')
                text = text.replace("&#39;", "'")
                return re.sub(r"\s+", " ", text).strip()

            # 1) Nouveau design GC.com : texte affiché dans srOnlyCacheNote / viewCacheNote
            display_patterns = [
                r"<div[^>]*id=\"srOnlyCacheNote\"[^>]*>(.*?)</div>",
                r"<button[^>]*id=\"viewCacheNote\"[^>]*>(.*?)</button>",
            ]

            for pattern in display_patterns:
                display_match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if display_match:
                    cleaned = _clean_note_text(display_match.group(1))
                    if cleaned:
                        logger.debug(
                            "Extracted personal note for %s via display pattern %s: %r",
                            gc_code,
                            pattern,
                            cleaned[:120],
                        )
                        return cleaned

            # 2) Ancien design : <textarea> avec id ou name contenant cacheNote
            textarea_patterns = [
                r"<textarea[^>]*(?:id|name)\s*=\s*\"[^\"]*cacheNote[^\"]*\"[^>]*>(.*?)</textarea>",
                r"<textarea[^>]*cacheNote[^>]*>(.*?)</textarea>",
            ]

            for pattern in textarea_patterns:
                textarea_match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
                if textarea_match:
                    cleaned = _clean_note_text(textarea_match.group(1))
                    if cleaned:
                        logger.debug(
                            "Extracted personal note for %s via textarea pattern %s: %r",
                            gc_code,
                            pattern,
                            cleaned[:120],
                        )
                        return cleaned

            # 3) Tentative sur des patterns JSON éventuels intégrés dans la page
            json_patterns = [
                r'"cacheNote"\s*:\s*"([^\"]*)"',
                r'"UserCacheNote"\s*:\s*"([^\"]*)"',
                r'"PersonalCacheNote"\s*:\s*"([^\"]*)"',
            ]

            for pattern in json_patterns:
                json_match = re.search(pattern, html, re.IGNORECASE)
                if json_match:
                    raw = json_match.group(1)
                    # Décoder les séquences d'échappement JSON simples (\n, \" ...)
                    try:
                        text = bytes(raw, "utf-8").decode("unicode_escape")
                    except Exception:
                        text = raw
                    cleaned = _clean_note_text(text)
                    logger.debug(
                        "Extracted personal note for %s via JSON pattern %s: %r",
                        gc_code,
                        pattern,
                        cleaned[:120],
                    )
                    return cleaned or None

            # 4) Logging de debug: essayer de trouver 'cacheNote' dans la page pour inspection ultérieure
            lower_html = html.lower()
            idx = lower_html.find("cachenote")
            if idx != -1:
                start = max(0, idx - 200)
                end = min(len(html), idx + 200)
                snippet = html[start:end]
                logger.warning(
                    "Personal note not parsed for %s, but 'cacheNote' found. HTML snippet: %r",
                    gc_code,
                    snippet,
                )
            else:
                logger.warning("Personal note not found and 'cacheNote' substring absent for %s", gc_code)

            return None
        except requests.RequestException as e:  # pragma: no cover
            logger.error("Failed to fetch personal note for %s: %s", gc_code, e)
            return None
