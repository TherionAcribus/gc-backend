from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
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
    # Enrichissements
    coordinates_raw: str | None = None
    is_corrected: bool | None = None
    original_latitude: float | None = None
    original_longitude: float | None = None
    description_html: str | None = None
    hints: str | None = None
    attributes: list[dict] | None = None
    favorites_count: int | None = None
    logs_count: int | None = None
    images: list[dict] | None = None
    waypoints: list[dict] = field(default_factory=list)
    checkers: list[dict] = field(default_factory=list)


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

        # Détecter le format de la page
        is_old_format = 'ctl00_ContentBody_CacheName' in resp.text
        is_new_format = 'data-testid' in resp.text
        logger.debug(f"Format détecté - Ancien ASP.NET: {is_old_format}, Nouveau React: {is_new_format}")

        # Adapter l'extraction selon le format
        if is_old_format:
            return self._scrape_old_format(code, soup, resp.text)
        else:
            return self._scrape_new_format(code, soup, resp.text)

    def _scrape_old_format(self, code: str, soup: BeautifulSoup, html_text: str) -> ScrapedGeocache:
        """Extraction pour l'ancien format ASP.NET (ctl00_ContentBody_*)"""
        logger.debug(f"Extraction ancien format ASP.NET pour {code}")

        def text_or_none(el):
            return el.get_text(strip=True) if el else None

        def parse_gc_coordinates(coords_text: str) -> tuple[Optional[float], Optional[float]]:
            try:
                parts = coords_text.split()
                if len(parts) < 6:
                    return None, None
                lat_dir = parts[0].upper()
                lat_deg = float(parts[1].replace('°', ''))
                lat_min = float(parts[2])
                lat = lat_deg + (lat_min / 60.0)
                if lat_dir == 'S':
                    lat = -lat
                lon_dir = parts[3].upper()
                lon_deg = float(parts[4].replace('°', ''))
                lon_min = float(parts[5])
                lon = lon_deg + (lon_min / 60.0)
                if lon_dir == 'W':
                    lon = -lon
                return lat, lon
            except Exception:
                return None, None

        # Nom de la cache
        name = None
        name_elem = soup.find('span', {'id': 'ctl00_ContentBody_CacheName'})
        if name_elem:
            name = name_elem.get_text(strip=True)
        if not name:
            name = code

        # Propriétaire
        owner_text = None
        owner_div = soup.find('div', {'id': 'ctl00_ContentBody_mcd1'})
        if owner_div:
            owner_link = owner_div.find('a')
            if owner_link:
                owner_text = owner_link.get_text(strip=True)

        # Type de cache
        type_text = None
        cache_link = soup.find('a', {'class': 'cacheImage'})
        if cache_link:
            title = cache_link.get('title', '')
            type_text = title.replace(' Cache', '').strip() if title else None

        # Taille
        size_text = None
        size_div = soup.find('div', {'class': 'CacheSize'})
        if size_div:
            size_img = size_div.find('img')
            if size_img:
                alt = size_img.get('alt', '')
                if ':' in alt:
                    size_text = alt.split(':')[-1].strip().lower()

        # Difficulté et terrain (ancien format)
        difficulty = None
        terrain = None

        # Chercher les labels texte
        diff_label = soup.find(string=lambda t: t and t.strip().lower() in ('difficulty:', 'difficulté:'))
        if diff_label:
            img = diff_label.find_next('img')
            if img:
                alt = img.get('alt', '')
                m = re.search(r'(\d+(?:[.,]\d+)?)', alt)
                if m:
                    difficulty = float(m.group(1).replace(',', '.'))

        terrain_label = soup.find(string=lambda t: t and t.strip().lower() == 'terrain:')
        if terrain_label:
            img = terrain_label.find_next('img')
            if img:
                alt = img.get('alt', '')
                m = re.search(r'(\d+(?:[.,]\d+)?)', alt)
                if m:
                    terrain = float(m.group(1).replace(',', '.'))

        # Coordonnées - chercher dans les scripts JavaScript
        latitude = None
        longitude = None
        coordinates_raw = None

        scripts = soup.find_all('script')
        for script in scripts:
            script_text = script.get_text() if script else ''
            if 'lat' in script_text.lower() and 'lon' in script_text.lower():
                # Chercher les vraies coordonnées
                lat_match = re.search(r'lat[^=]*=\s*([-\d.]+)', script_text, re.IGNORECASE)
                lon_match = re.search(r'lon[^=]*=\s*([-\d.]+)', script_text, re.IGNORECASE)
                if lat_match and lon_match:
                    try:
                        latitude = float(lat_match.group(1))
                        longitude = float(lon_match.group(1))
                        logger.debug(f"Coordonnées trouvées dans script: {latitude}, {longitude}")
                        break
                    except Exception:
                        continue

        # Date de placement
        placed_at = None
        date_div = soup.find('div', {'id': 'ctl00_ContentBody_mcd2'})
        if date_div:
            txt = date_div.get_text(strip=True)
            if ':' in txt:
                raw = txt.split(':', 1)[1].strip()
                for fmt in ('%d/%m/%Y', '%m/%d/%Y', '%Y-%m-%d'):
                    try:
                        placed_at = datetime.strptime(raw, fmt)
                        break
                    except Exception:
                        continue

        # Description
        description_html = None
        desc_el = soup.find('span', {'id': 'ctl00_ContentBody_LongDescription'})
        if desc_el:
            description_html = str(desc_el)

        # Indices
        hints = None
        hint_div = soup.find('div', {'id': 'div_hint'})
        if hint_div:
            hints = hint_div.get_text(strip=True)

        # Attributs
        attributes = []
        attrs_container = soup.find('div', {'class': 'WidgetBody'})
        if attrs_container:
            for img in attrs_container.find_all('img'):
                title = (img.get('title') or img.get('alt') or '').strip()
                if not title:
                    continue
                text_lower = title.lower()
                if 'blank' in text_lower:
                    continue
                is_negative = False
                name_only = title
                if ':' in title:
                    parts = title.split(':', 1)
                    name_only = parts[0].strip()
                    val = parts[1].strip().lower()
                    is_negative = 'no' in val or 'non' in val
                else:
                    if ' - no' in text_lower or ' - non' in text_lower:
                        is_negative = True
                        name_only = title.split(' - ')[0].strip()
                entry = {'name': name_only, 'is_negative': is_negative}
                attributes.append(entry)

        # Favoris - chercher dans les éléments avec "favorite"
        favorites_count = None
        fav_divs = soup.find_all('div', class_=lambda x: x and 'favorite' in ' '.join(x) if x else False)
        for div in fav_divs:
            text = div.get_text(strip=True).lower()
            if 'favorites' in text or 'favoris' in text:
                try:
                    digits = ''.join(ch for ch in text if ch.isdigit())
                    if digits:
                        favorites_count = int(digits)
                        break
                except Exception:
                    continue

        # Logs count
        logs_count = None
        all_text = soup.get_text()
        # Chercher "4 logs" ou similaire
        log_match = re.search(r'(\d+)\s*log', all_text, re.IGNORECASE)
        if log_match:
            logs_count = int(log_match.group(1))

        # Images
        images = []
        if desc_el:
            for img in desc_el.find_all('img'):
                src = img.get('src')
                if src and not any(s in src.lower() for s in ['wpttypes', 'icons', 'smilies']):
                    images.append({'url': src})

        return ScrapedGeocache(
            gc_code=code,
            name=name,
            url=f'{self.BASE_URL}{code}',
            type=type_text,
            size=size_text,
            owner=owner_text,
            difficulty=difficulty,
            terrain=terrain,
            latitude=latitude,
            longitude=longitude,
            placed_at=placed_at,
            status='active',
            coordinates_raw=coordinates_raw,
            description_html=description_html,
            hints=hints,
            attributes=attributes or None,
            favorites_count=favorites_count,
            logs_count=logs_count,
            images=images or None,
        )

    def _scrape_new_format(self, code: str, soup: BeautifulSoup, html_text: str) -> ScrapedGeocache:
        """Extraction pour le nouveau format React (data-testid)"""
        logger.debug(f"Extraction nouveau format React pour {code}")

        def text_or_none(el):
            return el.get_text(strip=True) if el else None

        def parse_gc_coordinates(coords_text: str) -> tuple[Optional[float], Optional[float]]:
            try:
                parts = coords_text.split()
                if len(parts) < 6:
                    return None, None
                lat_dir = parts[0].upper()
                lat_deg = float(parts[1].replace('°', ''))
                lat_min = float(parts[2])
                lat = lat_deg + (lat_min / 60.0)
                if lat_dir == 'S':
                    lat = -lat
                lon_dir = parts[3].upper()
                lon_deg = float(parts[4].replace('°', ''))
                lon_min = float(parts[5])
                lon = lon_deg + (lon_min / 60.0)
                if lon_dir == 'W':
                    lon = -lon
                return lat, lon
            except Exception:
                return None, None

        # Titre / nom
        name = None
        h1 = soup.find('h1')
        if h1:
            name = h1.get_text(strip=True)
        if not name:
            title_tag = soup.find('title')
            if title_tag:
                name = title_tag.get_text(strip=True)
        if not name:
            name = code

        # Type, taille, propriétaire, difficulté/terrain
        type_text = None
        size_text = None
        owner_text = None
        difficulty = None
        terrain = None

        # Sélecteurs modernes
        type_el = soup.select_one('[data-testid="cache-type"]')
        size_el = soup.select_one('[data-testid="container-size"]')
        owner_el = soup.select_one('[data-testid="owner-name"]')
        d_el = soup.select_one('[data-testid="difficulty"]')
        t_el = soup.select_one('[data-testid="terrain"]')

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

        # Coordonnées
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

        # Détection via script userDefinedCoords
        coordinates_raw = None
        is_corrected = None
        original_latitude = None
        original_longitude = None

        try:
            m = re.search(r'var\s+userDefinedCoords\s*=\s*(\{[\s\S]*?\});', html_text)
            if m:
                block = m.group(0)
                new_m = re.search(r'newLatLng\"?\s*:\s*\[\s*([-0-9\.]+)\s*,\s*([-0-9\.]+)\s*\]', block)
                old_m = re.search(r'oldLatLng\"?\s*:\s*\[\s*([-0-9\.]+)\s*,\s*([-0-9\.]+)\s*\]', block)
                is_def = re.search(r'isUserDefined\"?\s*:\s*(true|false)', block, re.I)
                if is_def and is_def.group(1).lower() == 'true':
                    is_corrected = True
                if new_m:
                    try:
                        latitude = float(new_m.group(1))
                        longitude = float(new_m.group(2))
                    except Exception:
                        pass
                if old_m:
                    try:
                        original_latitude = float(old_m.group(1))
                        original_longitude = float(old_m.group(2))
                    except Exception:
                        pass
        except Exception:
            pass

        # Date de placement
        placed_at = None
        date_el = soup.select_one('[data-testid="placed-on"], time[datetime]')
        if date_el and date_el.get('datetime'):
            try:
                placed_at = datetime.fromisoformat(date_el['datetime'].replace('Z', '+00:00'))
            except Exception:
                placed_at = None

        # Description
        description_html = None
        desc_el = soup.find('span', {'id': 'ctl00_ContentBody_LongDescription'})
        if desc_el:
            try:
                description_html = str(desc_el)
            except Exception:
                description_html = desc_el.get_text(strip=True)

        # Indices
        hints = None
        hint_div = soup.find('div', {'id': 'div_hint'})
        if hint_div:
            hints = hint_div.get_text(strip=True)

        # Attributs
        attributes = []
        attrs_container = soup.find('div', {'class': 'WidgetBody'})
        if attrs_container:
            for img in attrs_container.find_all('img'):
                title = (img.get('title') or img.get('alt') or '').strip()
                if not title:
                    continue
                text_lower = title.lower()
                if 'blank' in text_lower:
                    continue
                is_negative = False
                name_only = title
                if ':' in title:
                    parts = title.split(':', 1)
                    name_only = parts[0].strip()
                    val = parts[1].strip().lower()
                    is_negative = 'no' in val or 'non' in val
                else:
                    if ' - no' in text_lower or ' - non' in text_lower:
                        is_negative = True
                        name_only = title.split(' - ')[0].strip()
                entry = {'name': name_only, 'is_negative': is_negative}
                attributes.append(entry)

        # Favoris
        favorites_count = None
        fav_span = soup.find('span', {'class': 'favorite-value'})
        if fav_span:
            try:
                text = fav_span.get_text(strip=True)
                digits = ''.join(ch for ch in text if ch.isdigit())
                if digits:
                    favorites_count = int(digits)
            except Exception:
                pass

        # Logs
        logs_count = None
        link = soup.find('a', href=lambda x: x and 'geocache_logs.aspx' in x)
        if link:
            try:
                text = link.get_text() or ''
                digits = ''.join(ch for ch in text if ch.isdigit())
                if digits:
                    logs_count = int(digits)
            except Exception:
                pass

        # Images
        images = []
        if desc_el:
            for img in desc_el.find_all('img'):
                src = img.get('src')
                if src and not any(s in src.lower() for s in ['wpttypes', 'icons', 'smilies']):
                    images.append({'url': src})
        gallery = soup.find('div', {'class': 'CachePageImages'})
        if gallery:
            for img in gallery.find_all('img'):
                src = img.get('src')
                if src:
                    images.append({'url': src})

        return ScrapedGeocache(
            gc_code=code,
            name=name,
            url=f'{self.BASE_URL}{code}',
            type=type_text,
            size=size_text,
            owner=owner_text,
            difficulty=difficulty,
            terrain=terrain,
            latitude=latitude,
            longitude=longitude,
            placed_at=placed_at,
            status='active',
            coordinates_raw=coordinates_raw,
            is_corrected=is_corrected,
            original_latitude=original_latitude,
            original_longitude=original_longitude,
            description_html=description_html,
            hints=hints,
            attributes=attributes or None,
            favorites_count=favorites_count,
            logs_count=logs_count,
            images=images or None,
        )

