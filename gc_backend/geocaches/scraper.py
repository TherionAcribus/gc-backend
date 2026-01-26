from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

from ..utils.html_cleaner import html_to_text_with_linebreaks
from ..services.geocaching_auth import get_auth_service


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
    coordinates_raw: str | None = None  # Coordonnées affichées au format Geocaching (peuvent être corrigées)
    is_corrected: bool | None = None
    original_latitude: float | None = None  # Coordonnées originales en décimal (pour la carte)
    original_longitude: float | None = None  # Coordonnées originales en décimal (pour la carte)
    original_coordinates_raw: str | None = None  # Coordonnées originales au format Geocaching (format utilisé par les joueurs)
    description_html: str | None = None
    description_raw: str | None = None
    hints: str | None = None
    attributes: list[dict] | None = None
    favorites_count: int | None = None
    logs_count: int | None = None
    images: list[dict] | None = None
    waypoints: list[dict] = field(default_factory=list)
    checkers: list[dict] = field(default_factory=list)
    # Statut trouvé (facultatif)
    found: Optional[bool] = None
    found_date: Optional[datetime] = None


GC_CODE_RE = re.compile(r'^GC[0-9A-Z]+$')


class GeocachingScraper:
    BASE_URL = 'https://www.geocaching.com/geocache/'

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        # Utiliser la session du service d'authentification centralisé
        if session is not None:
            self.session = session
        else:
            auth_service = get_auth_service()
            self.session = auth_service.get_session()
            
            if not auth_service.is_logged_in():
                logger.warning("Not logged in to Geocaching.com - scraping may fail!")
                logger.warning("Please configure authentication in GeoApp preferences.")

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

        def text_or_none(el):
            return el.get_text(strip=True) if el else None

        # Nom (ancien format précis)
        logger.debug(f"[{code}] Extracting name...")
        name = None
        name_elem = soup.find('span', {'id': 'ctl00_ContentBody_CacheName'})
        if name_elem:
            name = name_elem.get_text(strip=True)
            logger.debug(f"[{code}] Name found via ctl00_ContentBody_CacheName: {name}")
        if not name:
            # Nouveau format
            h1 = soup.find('h1')
            if h1:
                name = h1.get_text(strip=True)
                logger.debug(f"[{code}] Name found via h1: {name}")
        if not name:
            title_tag = soup.find('title')
            if title_tag:
                name = title_tag.get_text(strip=True)
                logger.debug(f"[{code}] Name found via title: {name}")
        if not name:
            name = code
            logger.debug(f"[{code}] Name fallback to code: {name}")

        # Propriétaire (ancien format précis)
        logger.debug(f"[{code}] Extracting owner...")
        owner_text = None
        owner_div = soup.find('div', {'id': 'ctl00_ContentBody_mcd1'})
        if owner_div:
            owner_link = owner_div.find('a')
            if owner_link:
                owner_text = owner_link.get_text(strip=True)
                logger.debug(f"[{code}] Owner found via ctl00_ContentBody_mcd1: {owner_text}")
        if not owner_text:
            # Nouveau format fallback
            owner_el = soup.select_one('[data-testid="owner-name"], .owner-name a, .owner a')
            owner_text = text_or_none(owner_el)
            if owner_text:
                logger.debug(f"[{code}] Owner found via data-testid: {owner_text}")
        logger.debug(f"[{code}] Final owner: {owner_text}")

        # Type de cache (ancien format précis)
        logger.debug(f"[{code}] Extracting cache type...")
        type_text = None
        type_link = soup.find('a', {'class': 'cacheImage'})
        if type_link:
            type_text = type_link.get('title', '').replace(' Cache', '').strip()
            logger.debug(f"[{code}] Type found via cacheImage: {type_text}")
        if not type_text:
            # Nouveau format fallback
            type_el = soup.select_one('[data-testid="cache-type"], .cache-type')
            type_text = text_or_none(type_el)
            if type_text:
                logger.debug(f"[{code}] Type found via data-testid: {type_text}")
        logger.debug(f"[{code}] Final type: {type_text}")

        # Taille (ancien format précis)
        logger.debug(f"[{code}] Extracting size...")
        size_text = None
        size_div = soup.find('div', {'class': 'CacheSize'})
        if size_div:
            size_img = size_div.find('img')
            if size_img and size_img.get('alt'):
                raw_size = size_img['alt'].split(':')[-1].strip().lower()
                logger.debug(f"[{code}] Raw size from CacheSize: {raw_size}")
                # Conversion standardisée
                size_mapping = {
                    'micro': 'micro',
                    'small': 'small',
                    'regular': 'regular',
                    'large': 'large',
                    'very large': 'very_large',
                    'other': 'other',
                    'not chosen': 'unknown',
                    'virtual': 'virtual'
                }
                size_text = size_mapping.get(raw_size, raw_size)
                logger.debug(f"[{code}] Size mapped to: {size_text}")
        if not size_text:
            # Nouveau format fallback
            size_el = soup.select_one('[data-testid="container-size"], .cache-size')
            size_text = text_or_none(size_el)
            if size_text:
                logger.debug(f"[{code}] Size found via data-testid: {size_text}")
        logger.debug(f"[{code}] Final size: {size_text}")

        # Difficulté/Terrain (ancien format précis avec images)
        logger.debug(f"[{code}] Extracting difficulty/terrain...")
        difficulty = None
        terrain = None
        
        # Chercher les labels (FR/EN)
        difficulty_text_node = soup.find(string=lambda t: t and t.strip().lower() in ('difficulty:', 'difficulté:'))
        terrain_text_node = soup.find(string=lambda t: t and t.strip().lower() == 'terrain:')
        
        difficulty_img = None
        terrain_img = None
        
        if difficulty_text_node:
            difficulty_img = difficulty_text_node.find_next('img')
            logger.debug(f"[{code}] Difficulty img via label: {difficulty_img is not None}")
        if terrain_text_node:
            terrain_img = terrain_text_node.find_next('img')
            logger.debug(f"[{code}] Terrain img via label: {terrain_img is not None}")
        
        # Fallback: conteneur standard diffTerr
        if not difficulty_img or not terrain_img:
            container = soup.find('div', {'id': 'ctl00_ContentBody_diffTerr'})
            logger.debug(f"[{code}] diffTerr container found: {container is not None}")
            if container:
                dls = container.find_all('dl')
                logger.debug(f"[{code}] Found {len(dls)} dl elements in diffTerr")
                if len(dls) >= 2:
                    if not difficulty_img:
                        difficulty_img = dls[0].find('img')
                    if not terrain_img:
                        terrain_img = dls[1].find('img')
        
        if difficulty_img and difficulty_img.get('alt'):
            try:
                difficulty = float(difficulty_img['alt'].split()[0])
                logger.debug(f"[{code}] Difficulty extracted from img alt: {difficulty}")
            except Exception as e:
                logger.warning(f"[{code}] Failed to parse difficulty: {e}")
        if terrain_img and terrain_img.get('alt'):
            try:
                terrain = float(terrain_img['alt'].split()[0])
                logger.debug(f"[{code}] Terrain extracted from img alt: {terrain}")
            except Exception as e:
                logger.warning(f"[{code}] Failed to parse terrain: {e}")
        
        # Nouveau format fallback
        if difficulty is None or terrain is None:
            logger.debug(f"[{code}] Trying fallback for difficulty/terrain...")
            def parse_rating(txt: Optional[str]) -> Optional[float]:
                if not txt:
                    return None
                m = re.search(r'(\d+(?:[\.,]\d+)?)', txt)
                if m:
                    return float(m.group(1).replace(',', '.'))
                return None
            
            if difficulty is None:
                d_el = soup.select_one('[data-testid="difficulty"], .difficulty')
                difficulty = parse_rating(text_or_none(d_el))
                if difficulty:
                    logger.debug(f"[{code}] Difficulty from fallback: {difficulty}")
            if terrain is None:
                t_el = soup.select_one('[data-testid="terrain"], .terrain')
                terrain = parse_rating(text_or_none(t_el))
                if terrain:
                    logger.debug(f"[{code}] Terrain from fallback: {terrain}")
        
        logger.debug(f"[{code}] Final difficulty: {difficulty}, terrain: {terrain}")

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
        
        def decimal_to_gc_coordinates(lat: float, lon: float) -> str:
            """
            Convertit des coordonnées décimales en format Geocaching.
            Exemple: (48.856667, 2.350833) -> "N 48° 51.400 E 002° 21.050"
            """
            try:
                # Latitude
                lat_dir = 'N' if lat >= 0 else 'S'
                lat_abs = abs(lat)
                lat_deg = int(lat_abs)
                lat_min = (lat_abs - lat_deg) * 60.0
                
                # Longitude
                lon_dir = 'E' if lon >= 0 else 'W'
                lon_abs = abs(lon)
                lon_deg = int(lon_abs)
                lon_min = (lon_abs - lon_deg) * 60.0
                
                # Format: N 48° 51.400 E 002° 21.050
                return f"{lat_dir} {lat_deg}° {lat_min:.3f} {lon_dir} {lon_deg:03d}° {lon_min:.3f}"
            except Exception:
                return None

        # Extraction coordonnées (ancien format précis)
        logger.debug(f"[{code}] Extracting coordinates...")
        coordinates_raw = None
        is_corrected: Optional[bool] = None
        latitude = None
        longitude = None
        # Les coordonnées originales seront initialisées avec les coordonnées affichées
        # puis remplacées si des coordonnées corrigées sont détectées
        original_latitude: Optional[float] = None
        original_longitude: Optional[float] = None
        original_coordinates_raw: Optional[str] = None

        coords_span = soup.find('span', {'id': 'uxLatLon'})
        logger.debug(f"[{code}] uxLatLon span found: {coords_span is not None}")
        
        if coords_span:
            coordinates_raw = coords_span.get_text(strip=True)
            logger.debug(f"[{code}] coordinates_raw from uxLatLon: '{coordinates_raw}'")
            
            if not coordinates_raw or coordinates_raw == '???':
                logger.warning(f"[{code}] Coordinates are hidden or require authentication!")
            else:
                lat2, lon2 = parse_gc_coordinates(coordinates_raw)
                logger.debug(f"[{code}] Parsed coordinates: lat={lat2}, lon={lon2}")
                if lat2 is not None and lon2 is not None:
                    latitude = lat2
                    longitude = lon2
                    logger.debug(f"[{code}] Coordinates set successfully")
            
            # Détection coords corrigées par classe
            classes = coords_span.get('class') or []
            logger.debug(f"[{code}] uxLatLon classes: {classes}")
            if any('myLatLon' in c for c in classes):
                is_corrected = True
                logger.debug(f"[{code}] Detected corrected coordinates via myLatLon class")
        else:
            logger.warning(f"[{code}] uxLatLon span NOT found - coordinates may require authentication!")

        # Initialiser les coordonnées originales avec les coordonnées affichées
        # Elles seront remplacées si des coordonnées corrigées sont détectées
        original_coordinates_raw = coordinates_raw
        if latitude is not None and longitude is not None:
            original_latitude = latitude
            original_longitude = longitude
            logger.debug(f"[{code}] Initialized original coordinates with displayed coordinates")

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
                        # Convertir les coordonnées originales décimales en format Geocaching
                        original_coordinates_raw = decimal_to_gc_coordinates(original_latitude, original_longitude)
                        logger.debug(f"[{code}] Original coordinates in GC format: '{original_coordinates_raw}'")
                    except Exception:
                        pass
        except Exception:
            pass

        # Fallback meta tags (nouveau format)
        if latitude is None or longitude is None:
            geo_meta = soup.select_one('meta[property="place:location:latitude"]')
            if geo_meta and geo_meta.get('content'):
                try:
                    latitude = latitude or float(geo_meta['content'])
                except Exception:
                    pass
            geo_meta = soup.select_one('meta[property="place:location:longitude"]')
            if geo_meta and geo_meta.get('content'):
                try:
                    longitude = longitude or float(geo_meta['content'])
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
        description_raw = None
        desc_el = soup.find('span', {'id': 'ctl00_ContentBody_LongDescription'})
        if desc_el:
            try:
                description_html = str(desc_el)
                # Extraire le texte brut avec préservation des sauts de ligne
                description_raw = html_to_text_with_linebreaks(description_html)
            except Exception:
                description_html = desc_el.get_text(strip=True)
                description_raw = description_html

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
            checkers.append({'name': 'Geocaching', 'url': url})

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
            original_coordinates_raw=original_coordinates_raw,
            description_html=description_html,
            description_raw=description_raw,
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

        logger.info(f"Successfully scraped {code}: name='{name}', owner='{owner_text}', type='{type_text}', size='{size_text}', difficulty={difficulty}, terrain={terrain}, coords={latitude},{longitude}, favs={favorites_count}, logs={logs_count}")
        return scraped


