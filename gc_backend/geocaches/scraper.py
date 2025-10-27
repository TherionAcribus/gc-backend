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

        # Coordonnées brutes affichées (format GC: N 48° 51.402 E 002° 21.048)
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

        coordinates_raw = None
        is_corrected: Optional[bool] = None
        original_latitude: Optional[float] = None
        original_longitude: Optional[float] = None

        coords_span = soup.find('span', {'id': 'uxLatLon'})
        if coords_span:
            coordinates_raw = coords_span.get_text(strip=True)
            lat2, lon2 = parse_gc_coordinates(coordinates_raw)
            if lat2 is not None and lon2 is not None:
                latitude = latitude or lat2
                longitude = longitude or lon2
            # Détection coords corrigées par classe
            classes = coords_span.get('class') or []
            if any('myLatLon' in c for c in classes):
                is_corrected = True

        # Détection via script userDefinedCoords (si présent)
        try:
            m = re.search(r'userDefinedCoords\s*=\s*\{[\s\S]*?\};', resp.text)
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

        # Date de placement (si trouvée)
        placed_at = None
        date_el = soup.select_one('[data-testid="placed-on"], time[datetime]')
        if date_el and date_el.get('datetime'):
            try:
                placed_at = datetime.fromisoformat(date_el['datetime'].replace('Z', '+00:00'))
            except Exception:
                placed_at = None
        if not placed_at:
            # Fallback ancienne page: ctl00_ContentBody_mcd2 -> "Hidden: dd/mm/yyyy" ou "Hidden: mm/dd/yyyy"
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

        # Statut
        status = None
        status_el = soup.select_one('[data-testid="status"], .status')
        status = text_or_none(status_el)

        # Description HTML
        description_html = None
        desc_el = soup.find('span', {'id': 'ctl00_ContentBody_LongDescription'})
        if desc_el:
            try:
                description_html = str(desc_el)
            except Exception:
                description_html = desc_el.get_text(strip=True)

        # Hints
        hints = None
        hint_div = soup.find('div', {'id': 'div_hint'})
        if hint_div:
            hints = hint_div.get_text(strip=True)

        # Attributs
        attributes: list[dict] = []
        attrs_container = soup.find('div', {'class': 'WidgetBody'}) or soup.find('div', {'id': 'ctl00_ContentBody_detailWidget'}) or soup.find('div', {'id': 'ctl00_ContentBody_AttributesDiv'})
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
                base_filename = None
                src = img.get('src') or ''
                if src:
                    base = src.split('/')[-1]
                    base_filename = base.split('.')[0]
                entry = {'name': name_only, 'is_negative': is_negative}
                if base_filename:
                    entry['base_filename'] = base_filename
                attributes.append(entry)

        # Favoris
        favorites_count: Optional[int] = None
        fav_span = soup.find('span', {'class': 'favorite-value'})
        if not fav_span:
            fav_container = soup.find('div', {'class': 'favorite-container'})
            if fav_container:
                fav_span = fav_container.find('span', {'class': 'favorite-value'})
        if not fav_span:
            right = soup.find('div', {'class': 'favorite right'})
            if right:
                fav_span = right.find('span', {'class': 'favorite-value'})
        if not fav_span:
            for span in soup.find_all('span'):
                classes = span.get('class') or []
                if 'favorite-value' in classes:
                    fav_span = span
                    break
        if fav_span:
            try:
                favorites_count = int(''.join(ch for ch in fav_span.get_text(strip=True) if ch.isdigit()))
            except Exception:
                favorites_count = None

        # Logs count
        logs_count: Optional[int] = None
        link = soup.find('a', href=lambda x: x and 'geocache_logs.aspx' in x)
        if link:
            try:
                text = link.get_text() or ''
                digits = ''.join(ch for ch in text if ch.isdigit())
                if digits:
                    logs_count = int(digits)
            except Exception:
                logs_count = None

        # Waypoints additionnels
        waypoints: list[dict] = []
        wptable = soup.find('table', {'id': 'ctl00_ContentBody_Waypoints'})
        if wptable:
            rows = wptable.find_all('tr', {'class': 'BorderBottom', 'ishidden': 'false'})
            for row in rows:
                tds = row.find_all('td')
                if len(tds) < 6:
                    continue
                prefix = (tds[2].find('span').get_text(strip=True) if tds[2].find('span') else '').strip()
                lookup = tds[3].get_text(strip=True)
                name_cell = tds[4]
                name_link = name_cell.find('a')
                if name_link:
                    name_wp = name_link.get_text(strip=True)
                    type_text2 = name_cell.get_text().split('(')[-1].rstrip(')')
                else:
                    name_wp = name_cell.get_text(strip=True)
                    type_text2 = ''
                coords_text = tds[5].get_text(strip=True)
                lat_wp, lon_wp = (None, None)
                if coords_text:
                    lat_wp, lon_wp = parse_gc_coordinates(coords_text)
                note = ''
                note_row = row.find_next_sibling('tr', {'class': 'BorderBottom'})
                if note_row:
                    td_note = note_row.find('td', colspan=True)
                    if td_note:
                        note = td_note.get_text(strip=True)
                waypoints.append({
                    'prefix': prefix,
                    'lookup': lookup,
                    'name': name_wp,
                    'type': type_text2,
                    'latitude': lat_wp,
                    'longitude': lon_wp,
                    'gc_coords': coords_text,
                    'note': note
                })

        # Checkers externes
        checkers: list[dict] = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            low = href.lower()
            if any(dom in low for dom in ['geocheck.org', 'geotjek.dk', 'geo_inputchkcoord.php']):
                entry = {'name': 'GeoCheck', 'url': href}
                if entry not in checkers:
                    checkers.append(entry)
            elif 'certitudes.org' in low:
                entry = {'name': 'Certitude', 'url': href}
                if entry not in checkers:
                    checkers.append(entry)
        if soup.find('div', {'class': 'CoordChecker'}):
            checkers.append({'name': 'Geocaching', 'url': '#solution-checker'})

        # Images
        images: list[dict] = []
        if desc_el:
            for img in desc_el.find_all('img'):
                src = img.get('src')
                if not src:
                    continue
                low = src.lower()
                if any(s in low for s in ['wpttypes', 'icons', 'smilies']):
                    continue
                images.append({'url': src})
        gallery = soup.find('div', {'class': 'CachePageImages'})
        if gallery:
            for img in gallery.find_all('img'):
                src = img.get('src')
                if src:
                    images.append({'url': src})

        # Statut trouvé
        found = None
        found_date = None
        found_div = soup.find('div', {'id': 'ctl00_ContentBody_GeoNav_foundStatus'})
        if found_div:
            st = found_div.find('strong', {'id': 'ctl00_ContentBody_GeoNav_logText'})
            if st and ('Found It' in st.get_text()):
                found = True
                date_sm = found_div.find('small', {'id': 'ctl00_ContentBody_GeoNav_logDate'})
                if date_sm:
                    txt = date_sm.get_text()
                    if 'Logged on:' in txt:
                        raw = txt.split('Logged on:')[-1].strip()
                        for fmt in ('%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d'):
                            try:
                                found_date = datetime.strptime(raw, fmt)
                                break
                            except Exception:
                                continue

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
            waypoints=waypoints,
            checkers=checkers,
            # Passer aussi le statut trouvé
            # types stables: Optional[bool]/Optional[datetime]
            **({'found': found} if found is not None else {}),
            **({'found_date': found_date} if found_date is not None else {}),
        )

        logger.info(f"Successfully scraped {code}: name='{name}', type='{type_text}', coords={latitude},{longitude}")
        return scraped


