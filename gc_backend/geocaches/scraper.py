from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


@dataclass
class ScrapedGeocache:
    gc_code: str
    name: str
    url: str | None
    type: str | None
    size: str | None
    owner: str | None
    difficulty: float | None
    terrain: float | None
    latitude: float | None
    longitude: float | None
    placed_at: Optional[datetime]
    status: str | None


GC_CODE_RE = re.compile(r'^GC[0-9A-Z]+$')


class GeocachingScraper:
    BASE_URL = 'https://www.geocaching.com/geocache/'

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self.session = session or requests.Session()
        self.session.headers.setdefault('User-Agent', 'GeoApp/1.0 (+https://example.local)')

    @staticmethod
    def validate_gc_code(gc_code: str) -> str:
        code = (gc_code or '').strip().upper()
        if not GC_CODE_RE.match(code):
            raise ValueError('invalid_gc_code')
        return code

    def scrape(self, gc_code: str) -> ScrapedGeocache:
        code = self.validate_gc_code(gc_code)
        logger.info(f"Scraping geocache {code}")
        url = f'{self.BASE_URL}{code}'

        # Tentatives avec timeouts progressifs
        timeouts = [10, 20, 30]  # secondes
        last_exception = None

        for attempt, timeout in enumerate(timeouts, 1):
            logger.debug(f"Attempt {attempt}/{len(timeouts)} for {code} with timeout {timeout}s")

            try:
                logger.debug(f"Fetching URL: {url}")
                resp = self.session.get(url, timeout=timeout)
                logger.debug(f"HTTP response status: {resp.status_code}")

                if resp.status_code == 404:
                    logger.warning(f"Geocache {code} not found (404)")
                    raise LookupError('gc_not_found')
                resp.raise_for_status()

                # Succès - sortir de la boucle
                break

            except requests.exceptions.Timeout as e:
                last_exception = e
                if attempt < len(timeouts):
                    logger.warning(f"Timeout attempt {attempt} for {code} ({timeout}s), retrying...")
                    time.sleep(1)  # Petite pause avant retry
                else:
                    logger.error(f"All timeout attempts failed for {code}")
                    raise LookupError('gc_timeout') from e

            except requests.RequestException as e:
                logger.error(f"HTTP request failed for {code}: {e}")
                raise

        else:
            # Si on arrive ici, c'est qu'on a épuisé tous les timeouts
            if last_exception:
                raise LookupError('gc_timeout') from last_exception
            raise RuntimeError(f"Unexpected error scraping {code}")

        logger.debug(f"Parsing HTML for {code}")
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Titre / nom
        name = None
        h1 = soup.find('h1')
        if h1:
            name = h1.get_text(strip=True)
        if not name:
            # Fallback: meta title
            title_tag = soup.find('title')
            if title_tag:
                name = title_tag.get_text(strip=True)
        if not name:
            name = code

        # Type, taille, propriétaire, difficulté/terrain
        def text_or_none(el):
            return el.get_text(strip=True) if el else None

        type_text = None
        size_text = None
        owner_text = None
        difficulty = None
        terrain = None

        # Heuristiques basiques (les sélecteurs exacts pourront être raffinés)
        type_el = soup.select_one('[data-testid="cache-type"], .cache-type')
        size_el = soup.select_one('[data-testid="container-size"], .cache-size')
        owner_el = soup.select_one('[data-testid="owner-name"], .owner-name a, .owner a')
        d_el = soup.select_one('[data-testid="difficulty"], .difficulty')
        t_el = soup.select_one('[data-testid="terrain"], .terrain')

        type_text = text_or_none(type_el)
        size_text = text_or_none(size_el)
        owner_text = text_or_none(owner_el)

        def parse_rating(txt: Optional[str]) -> Optional[float]:
            if not txt:
                return None
            m = re.search(r'(\d+(?:[\.,]\d+)?)', txt)
            if m:
                return float(m.group(1).replace(',', '.'))
            return None

        difficulty = parse_rating(text_or_none(d_el))
        terrain = parse_rating(text_or_none(t_el))

        # Coords: chercher microformats ou liens map
        latitude = None
        longitude = None
        geo_meta = soup.select_one('meta[property="place:location:latitude"]')
        if geo_meta and geo_meta.get('content'):
            try:
                latitude = float(geo_meta['content'])
            except Exception:
                pass
        geo_meta = soup.select_one('meta[property="place:location:longitude"]')
        if geo_meta and geo_meta.get('content'):
            try:
                longitude = float(geo_meta['content'])
            except Exception:
                pass

        # Date de placement (si trouvée)
        placed_at = None
        date_el = soup.select_one('[data-testid="placed-on"], time[datetime]')
        if date_el and date_el.get('datetime'):
            try:
                placed_at = datetime.fromisoformat(date_el['datetime'].replace('Z', '+00:00'))
            except Exception:
                placed_at = None

        # Statut
        status = None
        status_el = soup.select_one('[data-testid="status"], .status')
        status = text_or_none(status_el)

        scraped = ScrapedGeocache(
            gc_code=code,
            name=name,
            url=url,
            type=type_text,
            size=size_text,
            owner=owner_text,
            difficulty=difficulty,
            terrain=terrain,
            latitude=latitude,
            longitude=longitude,
            placed_at=placed_at,
            status=status,
        )

        logger.info(f"Successfully scraped {code}: name='{name}', type='{type_text}', coords={latitude},{longitude}")
        return scraped


