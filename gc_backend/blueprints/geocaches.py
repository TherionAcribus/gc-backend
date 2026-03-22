from flask import Blueprint, jsonify, request, Response, stream_with_context
import logging
import io
import time
import zipfile
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import json
from datetime import datetime, timezone
import re

from ..database import db
from ..geocaches.models import Geocache
from ..geocaches.importer import GeocacheImporter
from ..geocaches.archive_service import ArchiveService
from ..geocaches.scraper import GeocachingScraper
from ..geocaches.search_client import GeocachingSearchClient
from ..geocaches.image_storage import remove_geocache_dir
from ..geocaches.image_sync import ensure_images_v2_for_geocache
from ..geocaches.bookmark_list_importer import BookmarkListImporter
from ..geocaches.pocket_query_importer import PocketQueryImporter
from ..utils.preferences import get_value_or_default

bp = Blueprint('geocaches', __name__)
logger = logging.getLogger(__name__)

def _as_gpx_time(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _groundspeak_cache_type(raw_type: str | None) -> str:
    value = (raw_type or '').strip()
    if not value:
        return 'Unknown Cache'

    normalized = value.lower().strip()

    # If it already looks like a Groundspeak label, keep it.
    already = {
        'traditional cache',
        'unknown (mystery) cache',
        'unknown cache',
        'multi-cache',
        'virtual cache',
        'event cache',
        'cito event cache',
        'mega-event cache',
        'giga-event cache',
        'webcam cache',
        'wherigo cache',
        'earthcache',
        'letterbox hybrid',
        'gps adventures exhibit',
    }
    if normalized in already:
        return value

    mapping = {
        'traditional': 'Traditional Cache',
        'tradi': 'Traditional Cache',
        'mystery': 'Unknown (Mystery) Cache',
        'unknown': 'Unknown (Mystery) Cache',
        'puzzle': 'Unknown (Mystery) Cache',
        'multi': 'Multi-cache',
        'multi-cache': 'Multi-cache',
        'virtual': 'Virtual Cache',
        'event': 'Event Cache',
        'cito': 'CITO Event Cache',
        'mega': 'Mega-Event Cache',
        'giga': 'Giga-Event Cache',
        'webcam': 'Webcam Cache',
        'wherigo': 'Wherigo Cache',
        'earthcache': 'EarthCache',
        'earth': 'EarthCache',
        'letterbox': 'Letterbox Hybrid',
        'letterbox hybrid': 'Letterbox Hybrid',
    }

    return mapping.get(normalized, value)


def _groundspeak_internal_cache_type(raw_type: str | None) -> str:
    """Return value for <groundspeak:type>.

    Groundspeak GPX uses 'Unknown Cache' for puzzles/mystery caches.
    """
    display = _groundspeak_cache_type(raw_type)
    if display.lower() == 'unknown (mystery) cache':
        return 'Unknown Cache'
    return display


def _groundspeak_log_type(raw_type: str | None) -> str:
    value = (raw_type or '').strip()
    if not value:
        return 'Write note'

    normalized = value.lower().strip()
    mapping = {
        'found': 'Found it',
        'found it': 'Found it',
        'did not find': "Didn't find it",
        "didn't find": "Didn't find it",
        "didn't find it": "Didn't find it",
        'note': 'Write note',
        'write note': 'Write note',
        'owner maintenance': 'Owner Maintenance',
        'needs maintenance': 'Needs Maintenance',
        'needs archived': 'Needs Archived',
        'will attend': 'Will Attend',
        'attended': 'Attended',
        'enabled': 'Enabled',
        'temporarily disabled': 'Temporarily Disable Listing',
        'published': 'Publish Listing',
        'retracted': 'Retract Listing',
        'archived': 'Archive',
        'unarchived': 'Unarchive',
        'reviewer note': 'Reviewer Note',
    }
    if normalized in mapping:
        return mapping[normalized]

    return value


def _to_pretty_xml_bytes(root: ET.Element) -> bytes:
    if hasattr(ET, 'indent'):
        ET.indent(root, space='  ', level=0)
        return ET.tostring(root, encoding='utf-8', xml_declaration=True)

    raw = ET.tostring(root, encoding='utf-8')
    return minidom.parseString(raw).toprettyxml(indent='  ', encoding='utf-8')


def _safe_groundspeaks_bool(value: bool) -> str:
    return 'True' if value else 'False'


def _build_groundspeak_gpx_bytes(geocaches: list[Geocache]) -> bytes:
    gpx = ET.Element('gpx')
    gpx.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
    gpx.set('xmlns:xsd', 'http://www.w3.org/2001/XMLSchema')
    gpx.set('version', '1.0')
    gpx.set('creator', 'GeoApp')
    gpx.set('xmlns', 'http://www.topografix.com/GPX/1/0')
    gpx.set('xmlns:groundspeak', 'http://www.groundspeak.com/cache/1/0/1')
    gpx.set(
        'xsi:schemaLocation',
        'http://www.topografix.com/GPX/1/0 http://www.topografix.com/GPX/1/0/gpx.xsd '
        'http://www.groundspeak.com/cache/1/0/1 http://www.groundspeak.com/cache/1/0/1/cache.xsd',
    )

    name = ET.SubElement(gpx, 'name')
    name.text = f"GeoApp Export {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    desc = ET.SubElement(gpx, 'desc')
    desc.text = 'Geocache file generated by GeoApp'
    author = ET.SubElement(gpx, 'author')
    author.text = 'GeoApp'
    time_tag = ET.SubElement(gpx, 'time')
    time_tag.text = _as_gpx_time(datetime.now(timezone.utc))
    keywords = ET.SubElement(gpx, 'keywords')
    keywords.text = 'cache, geocache, geoapp'

    lats: list[float] = []
    lons: list[float] = []

    notes_mode = str(get_value_or_default('geoApp.gpxExport.notesMode', 'logs') or 'logs').strip().lower()
    include_gc_logs = bool(get_value_or_default('geoApp.gpxExport.includeGeocachingLogs', True))
    max_gc_logs = int(get_value_or_default('geoApp.gpxExport.maxGeocachingLogs', 5) or 0)

    for geocache in geocaches:
        lat = geocache.latitude
        lon = geocache.longitude
        if lat is None or lon is None:
            continue

        lats.append(float(lat))
        lons.append(float(lon))

        wpt = ET.SubElement(gpx, 'wpt')
        wpt.set('lat', str(lat))
        wpt.set('lon', str(lon))

        wpt_time = ET.SubElement(wpt, 'time')
        wpt_time.text = _as_gpx_time(geocache.placed_at)

        wpt_name = ET.SubElement(wpt, 'name')
        wpt_name.text = geocache.gc_code

        owner = (geocache.owner or '').strip() or 'Unknown'
        cache_type = _groundspeak_cache_type(geocache.type)
        gs_cache_type = _groundspeak_internal_cache_type(geocache.type)
        difficulty = geocache.difficulty
        terrain = geocache.terrain
        dt = f"{difficulty:.1f}" if isinstance(difficulty, (int, float)) else '?'
        tt = f"{terrain:.1f}" if isinstance(terrain, (int, float)) else '?'

        wpt_desc = ET.SubElement(wpt, 'desc')
        wpt_desc.text = f"{geocache.name} by {owner}, {cache_type} ({dt}/{tt})"

        wpt_url = ET.SubElement(wpt, 'url')
        wpt_url.text = f"https://coord.info/{geocache.gc_code}"

        wpt_urlname = ET.SubElement(wpt, 'urlname')
        wpt_urlname.text = geocache.name

        wpt_sym = ET.SubElement(wpt, 'sym')
        wpt_sym.text = 'Geocache'

        wpt_type = ET.SubElement(wpt, 'type')
        wpt_type.text = f"Geocache|{cache_type}"

        gs_cache = ET.SubElement(wpt, 'groundspeak:cache')
        gs_cache.set('id', str(geocache.id))
        gs_cache.set('archived', _safe_groundspeaks_bool((geocache.status or '').lower() == 'archived'))
        gs_cache.set('available', _safe_groundspeaks_bool((geocache.status or '').lower() != 'archived'))

        gs_name = ET.SubElement(gs_cache, 'groundspeak:name')
        gs_name.text = geocache.name

        gs_placed_by = ET.SubElement(gs_cache, 'groundspeak:placed_by')
        gs_placed_by.text = owner

        gs_owner = ET.SubElement(gs_cache, 'groundspeak:owner')
        gs_owner.set('id', '0')
        gs_owner.text = owner

        gs_type = ET.SubElement(gs_cache, 'groundspeak:type')
        gs_type.text = gs_cache_type

        if geocache.size:
            gs_container = ET.SubElement(gs_cache, 'groundspeak:container')
            gs_container.text = geocache.size

        if isinstance(geocache.attributes, list) and geocache.attributes:
            gs_attributes = ET.SubElement(gs_cache, 'groundspeak:attributes')
            for i, attr in enumerate(geocache.attributes, start=1):
                if not isinstance(attr, dict):
                    continue
                name_attr = (attr.get('name') or '').strip()
                if not name_attr:
                    continue
                is_negative = bool(attr.get('is_negative'))
                gs_attr = ET.SubElement(gs_attributes, 'groundspeak:attribute')
                gs_attr.set('id', str(i))
                gs_attr.set('inc', '0' if is_negative else '1')
                gs_attr.text = name_attr

        if isinstance(difficulty, (int, float)):
            gs_difficulty = ET.SubElement(gs_cache, 'groundspeak:difficulty')
            gs_difficulty.text = f"{float(difficulty):.1f}"

        if isinstance(terrain, (int, float)):
            gs_terrain = ET.SubElement(gs_cache, 'groundspeak:terrain')
            gs_terrain.text = f"{float(terrain):.1f}"

        listing_html = geocache.description_html or geocache.description_raw or ''
        if notes_mode == 'listing' and geocache.notes:
            sorted_notes = sorted(
                [n for n in (geocache.notes or []) if getattr(n, 'content', None)],
                key=lambda n: getattr(n, 'updated_at', None) or getattr(n, 'created_at', None) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            if sorted_notes:
                notes_block = '\n'.join(
                    f"<p><b>[GeoApp Note - {getattr(n, 'note_type', 'note')}]</b><br/>{getattr(n, 'content', '')}</p>" for n in sorted_notes
                )
                listing_html = f"{listing_html}\n<hr/>\n{notes_block}"

        gs_short_desc = ET.SubElement(gs_cache, 'groundspeak:short_description')
        gs_short_desc.set('html', 'True')
        gs_short_desc.text = ''

        gs_long_desc = ET.SubElement(gs_cache, 'groundspeak:long_description')
        gs_long_desc.set('html', 'True')
        gs_long_desc.text = listing_html

        gs_hints = ET.SubElement(gs_cache, 'groundspeak:encoded_hints')
        gs_hints.text = geocache.hints or ''

        wants_note_logs = notes_mode == 'logs'
        has_notes = bool(geocache.notes)
        wants_gc_logs = include_gc_logs and max_gc_logs > 0
        has_logs = bool(geocache.logs) and wants_gc_logs

        if (wants_note_logs and has_notes) or has_logs:
            gs_logs = ET.SubElement(gs_cache, 'groundspeak:logs')

            if wants_note_logs and has_notes:
                sorted_notes = sorted(
                    [n for n in (geocache.notes or []) if getattr(n, 'content', None)],
                    key=lambda n: getattr(n, 'updated_at', None) or getattr(n, 'created_at', None) or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                for note in sorted_notes:
                    gs_log = ET.SubElement(gs_logs, 'groundspeak:log')
                    gs_log.set('id', str(1000000000 + int(getattr(note, 'id', 0) or 0)))

                    gs_log_date = ET.SubElement(gs_log, 'groundspeak:date')
                    gs_log_date.text = _as_gpx_time(getattr(note, 'updated_at', None) or getattr(note, 'created_at', None))

                    gs_log_type = ET.SubElement(gs_log, 'groundspeak:type')
                    gs_log_type.text = 'Write note'

                    gs_log_finder = ET.SubElement(gs_log, 'groundspeak:finder')
                    gs_log_finder.set('id', '0')
                    gs_log_finder.text = 'GeoApp'

                    gs_log_text = ET.SubElement(gs_log, 'groundspeak:text')
                    gs_log_text.set('encoded', 'False')
                    note_type = getattr(note, 'note_type', None) or 'note'
                    gs_log_text.text = f"[{note_type}] {getattr(note, 'content', '')}"

            if has_logs:
                for log in (geocache.logs or [])[:max_gc_logs]:
                    gs_log = ET.SubElement(gs_logs, 'groundspeak:log')
                    external_id = getattr(log, 'external_id', None)
                    gs_log.set('id', str(external_id or getattr(log, 'id', 0)))

                    gs_log_date = ET.SubElement(gs_log, 'groundspeak:date')
                    gs_log_date.text = _as_gpx_time(getattr(log, 'date', None))

                    gs_log_type = ET.SubElement(gs_log, 'groundspeak:type')
                    gs_log_type.text = _groundspeak_log_type(getattr(log, 'log_type', None))

                    gs_log_finder = ET.SubElement(gs_log, 'groundspeak:finder')
                    gs_log_finder.set('id', '0')
                    gs_log_finder.text = getattr(log, 'author', None) or 'Unknown'

                    gs_log_text = ET.SubElement(gs_log, 'groundspeak:text')
                    gs_log_text.set('encoded', 'False')
                    gs_log_text.text = getattr(log, 'text', None) or ''

    if lats and lons:
        bounds = ET.SubElement(gpx, 'bounds')
        bounds.set('minlat', str(min(lats)))
        bounds.set('minlon', str(min(lons)))
        bounds.set('maxlat', str(max(lats)))
        bounds.set('maxlon', str(max(lons)))

    return _to_pretty_xml_bytes(gpx)


def _build_waypoints_gpx_bytes(geocaches: list[Geocache]) -> bytes | None:
    all_waypoints = []
    for geocache in geocaches:
        for w in (geocache.waypoints or []):
            all_waypoints.append(w)

    if not all_waypoints:
        return None

    gpx = ET.Element('gpx')
    gpx.set('xmlns:xsi', 'http://www.w3.org/2001/XMLSchema-instance')
    gpx.set('xmlns:xsd', 'http://www.w3.org/2001/XMLSchema')
    gpx.set('version', '1.0')
    gpx.set('creator', 'GeoApp')
    gpx.set('xmlns', 'http://www.topografix.com/GPX/1/0')
    gpx.set('xsi:schemaLocation', 'http://www.topografix.com/GPX/1/0 http://www.topografix.com/GPX/1/0/gpx.xsd')

    name = ET.SubElement(gpx, 'name')
    name.text = 'Waypoints for Cache Listings Generated from GeoApp'
    desc = ET.SubElement(gpx, 'desc')
    desc.text = 'This is a list of supporting waypoints for caches generated from GeoApp'
    author = ET.SubElement(gpx, 'author')
    author.text = 'GeoApp'
    time_tag = ET.SubElement(gpx, 'time')
    time_tag.text = _as_gpx_time(datetime.now(timezone.utc))
    keywords = ET.SubElement(gpx, 'keywords')
    keywords.text = 'cache, geocache, waypoints'

    lats: list[float] = []
    lons: list[float] = []

    for waypoint in all_waypoints:
        lat = getattr(waypoint, 'latitude', None)
        lon = getattr(waypoint, 'longitude', None)
        if lat is None or lon is None:
            continue
        lats.append(float(lat))
        lons.append(float(lon))

        wpt = ET.SubElement(gpx, 'wpt')
        wpt.set('lat', str(lat))
        wpt.set('lon', str(lon))

        time_elem = ET.SubElement(wpt, 'time')
        time_elem.text = _as_gpx_time(datetime.now(timezone.utc))

        wp_name = ET.SubElement(wpt, 'name')
        prefix = (getattr(waypoint, 'prefix', None) or '').strip()
        lookup = (getattr(waypoint, 'lookup', None) or '').strip()
        if lookup:
            wp_name.text = lookup
        elif prefix:
            wp_name.text = f"{prefix}{waypoint.geocache.gc_code}"
        else:
            wp_name.text = f"WP{getattr(waypoint, 'id', 0)}_{waypoint.geocache.gc_code}"

        wp_desc = ET.SubElement(wpt, 'desc')
        wp_desc.text = getattr(waypoint, 'name', None) or 'Additional Waypoint'

        wp_url = ET.SubElement(wpt, 'url')
        wp_url.text = f"http://www.geocaching.com/seek/wpt.aspx?WID={getattr(waypoint, 'id', 0)}"

        wp_urlname = ET.SubElement(wpt, 'urlname')
        wp_urlname.text = getattr(waypoint, 'name', None) or 'Additional Waypoint'

        wp_sym = ET.SubElement(wpt, 'sym')
        wp_type = ET.SubElement(wpt, 'type')

        sym = 'Reference Point'
        if prefix:
            p = prefix.lower()
            if p.startswith('pk') or p.startswith('p'):
                sym = 'Parking Area'
            elif p.startswith('st') or p.startswith('s'):
                sym = 'Stages of a Multicache'
            elif p.startswith('fn') or p.startswith('f'):
                sym = 'Final Location'
            elif p.startswith('tr') or p.startswith('t'):
                sym = 'Trailhead'

        wp_sym.text = sym
        wp_type.text = f"Waypoint|{sym}"

        wp_cmt = ET.SubElement(wpt, 'cmt')
        wp_cmt.text = getattr(waypoint, 'note', None) or ''

    if lats and lons:
        bounds = ET.SubElement(gpx, 'bounds')
        bounds.set('minlat', str(min(lats)))
        bounds.set('minlon', str(min(lons)))
        bounds.set('maxlat', str(max(lats)))
        bounds.set('maxlon', str(max(lons)))

    return ET.tostring(gpx, encoding='utf-8', xml_declaration=True)


def _get_center_from_request_payload(data: dict) -> tuple[float, float]:
    center = data.get('center') if isinstance(data, dict) else None
    if isinstance(center, dict):
        center_type = (center.get('type') or '').strip().lower()
        if center_type == 'point':
            lat = center.get('lat')
            lon = center.get('lon')
            if lat is not None and lon is not None:
                return float(lat), float(lon)

        if center_type == 'geocache_id':
            geocache_id = center.get('geocache_id')
            if geocache_id is not None:
                geocache = Geocache.query.get(int(geocache_id))
                if not geocache:
                    raise LookupError('geocache_not_found')
                if geocache.latitude is None or geocache.longitude is None:
                    raise ValueError('geocache_has_no_coordinates')
                return float(geocache.latitude), float(geocache.longitude)

        if center_type == 'gc_code':
            gc_code = (center.get('gc_code') or '').strip().upper()
            if gc_code:
                existing = Geocache.query.filter(Geocache.gc_code == gc_code).first()
                if existing and existing.latitude is not None and existing.longitude is not None:
                    return float(existing.latitude), float(existing.longitude)

                scraper = GeocachingScraper()
                scraped = scraper.scrape(gc_code)
                if scraped.latitude is None or scraped.longitude is None:
                    raise ValueError('geocache_has_no_coordinates')
                return float(scraped.latitude), float(scraped.longitude)

        lat = center.get('lat')
        lon = center.get('lon')
        if lat is not None and lon is not None:
            return float(lat), float(lon)

    geocache_id = data.get('geocache_id')
    if geocache_id is not None:
        geocache = Geocache.query.get(int(geocache_id))
        if not geocache:
            raise LookupError('geocache_not_found')
        if geocache.latitude is None or geocache.longitude is None:
            raise ValueError('geocache_has_no_coordinates')
        return float(geocache.latitude), float(geocache.longitude)

    gc_code = (data.get('gc_code') or '').strip().upper()
    if gc_code:
        existing = Geocache.query.filter(Geocache.gc_code == gc_code).first()
        if existing and existing.latitude is not None and existing.longitude is not None:
            return float(existing.latitude), float(existing.longitude)

        scraper = GeocachingScraper()
        scraped = scraper.scrape(gc_code)
        if scraped.latitude is None or scraped.longitude is None:
            raise ValueError('geocache_has_no_coordinates')
        return float(scraped.latitude), float(scraped.longitude)

    raise ValueError('missing_center')


@bp.get('/api/zones/<int:zone_id>/geocaches')
def get_geocaches_for_zone(zone_id: int):
    """Récupère toutes les géocaches d'une zone."""
    try:
        geocaches = Geocache.query.filter_by(zone_id=zone_id).all()
        
        # Adapter les données au format attendu par le frontend
        result = []
        for gc in geocaches:
            result.append({
                'id': gc.id,
                'gc_code': gc.gc_code,
                'name': gc.name,
                'description': gc.description_raw,
                'hint': gc.hints,
                'owner': gc.owner,
                'cache_type': gc.type,  # Le frontend attend 'cache_type'
                'difficulty': gc.difficulty,
                'terrain': gc.terrain,
                'size': gc.size,
                'solved': gc.solved or 'not_solved',
                'found': gc.found or False,
                'favorites_count': gc.favorites_count or 0,
                'hidden_date': gc.placed_at.isoformat() if gc.placed_at else None,
                'latitude': gc.latitude,
                'longitude': gc.longitude,
                'coordinates_raw': gc.coordinates_raw,
                'is_corrected': gc.is_corrected or False,
                'original_latitude': gc.original_latitude,
                'original_longitude': gc.original_longitude,
                'waypoints': [w.to_dict() for w in (gc.waypoints or [])],
            })
        
        logger.info(f"Returning {len(result)} geocaches for zone {zone_id}")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error fetching geocaches for zone {zone_id}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/export-gpx')
def export_gpx():
    data = request.get_json(silent=True) or {}
    ids_raw = data.get('geocache_ids')
    if not isinstance(ids_raw, list) or not ids_raw:
        return jsonify({'error': 'Missing required field: geocache_ids'}), 400

    try:
        geocache_ids = [int(x) for x in ids_raw]
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid geocache_ids'}), 400

    geocaches = Geocache.query.filter(Geocache.id.in_(geocache_ids)).all()
    if not geocaches:
        return jsonify({'error': 'No geocaches found'}), 404

    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    filename = (data.get('filename') or '').strip() or f'geoapp_geocaches_{timestamp}.gpx'
    filename = filename.replace('\\', '_').replace('/', '_')
    filename = re.sub(r'[^A-Za-z0-9._-]+', '_', filename)
    filename = filename.strip('._-') or f'geoapp_geocaches_{timestamp}.gpx'
    if not filename.lower().endswith('.gpx'):
        filename = f'{filename}.gpx'

    base_name = filename[:-4]

    main_payload = _build_groundspeak_gpx_bytes(geocaches)
    wpts_payload = _build_waypoints_gpx_bytes(geocaches)

    if wpts_payload:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f'{base_name}.gpx', main_payload)
            zf.writestr(f'{base_name}-wpts.gpx', wpts_payload)
        zip_buffer.seek(0)

        zip_filename = f'{base_name}.zip'
        resp = Response(zip_buffer.getvalue(), mimetype='application/zip')
        resp.headers['Content-Disposition'] = f'attachment; filename="{zip_filename}"; filename*=UTF-8\'\'{zip_filename}'
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        return resp

    resp = Response(main_payload, mimetype='application/octet-stream')
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp


@bp.post('/api/geocaches/import-around')
def import_around():
    """Importe plusieurs géocaches autour d'un point ou d'une géocache.

    Réponse en streaming JSON (une ligne = un objet JSON) compatible avec le frontend.
    """
    try:
        data = request.get_json(silent=True) or {}

        zone_id = data.get('zone_id')
        if not zone_id:
            return jsonify({'error': 'Missing required field: zone_id'}), 400
        try:
            zone_id = int(zone_id)
        except ValueError:
            return jsonify({'error': 'Invalid zone_id'}), 400

        try:
            center_lat, center_lon = _get_center_from_request_payload(data)
        except LookupError as e:
            if 'geocache_not_found' in str(e):
                return jsonify({'error': 'Geocache not found'}), 404
            return jsonify({'error': str(e)}), 400
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

        limit = data.get('limit', 50)
        radius_km = data.get('radius_km')
        try:
            limit = int(limit)
        except ValueError:
            return jsonify({'error': 'Invalid limit'}), 400

        if radius_km is not None and str(radius_km).strip() != '':
            try:
                radius_km = float(radius_km)
            except ValueError:
                return jsonify({'error': 'Invalid radius_km'}), 400
        else:
            radius_km = None

        if limit <= 0:
            return jsonify({'error': 'limit must be > 0'}), 400
        if radius_km is not None and radius_km <= 0:
            return jsonify({'error': 'radius_km must be > 0'}), 400

        importer = GeocacheImporter()
        search_client = GeocachingSearchClient(session=importer.scraper.session)

        def generate():
            try:
                yield json.dumps({'message': 'Recherche des géocaches autour...', 'progress': 0}) + '\n'

                results = search_client.search(
                    center_lat=center_lat,
                    center_lon=center_lon,
                    limit=limit,
                    radius_km=radius_km,
                )
                gc_codes = [r.gc_code for r in results]
                total = len(gc_codes)

                if total == 0:
                    yield json.dumps({'error': True, 'message': 'Aucune géocache trouvée'}) + '\n'
                    return

                yield json.dumps({'message': f'{total} géocache(s) trouvée(s)', 'progress': 10}) + '\n'
                yield json.dumps({'message': f'Import de {total} géocache(s)...', 'progress': 15}) + '\n'

                success = 0
                errors = 0

                for idx, code in enumerate(gc_codes, start=1):
                    try:
                        importer.import_by_code(zone_id, code)
                        success += 1
                        msg = f'Importée: {code} ({idx}/{total})'
                    except Exception as e:
                        errors += 1
                        msg = f'Erreur {code}: {e}'

                    pct = 15 + int(idx / total * 85)
                    yield json.dumps({'message': msg, 'progress': pct}) + '\n'

                    time.sleep(0.2)

                summary = f'Importation terminée: {success} succès'
                if errors:
                    summary += f', {errors} erreurs'

                yield json.dumps({
                    'progress': 100,
                    'message': summary,
                    'final_summary': True,
                    'stats': {'success': success, 'errors': errors, 'total': total},
                    'center': {'lat': center_lat, 'lon': center_lon},
                    **({'radius_km': radius_km} if radius_km is not None else {}),
                }) + '\n'

            except Exception as e:
                logger.error(f"Erreur import-around: {e}", exc_info=True)
                yield json.dumps({'error': True, 'message': f'Erreur: {str(e)}'}) + '\n'

        return Response(stream_with_context(generate()), content_type='application/json')

    except Exception as e:
        logger.error(f"Erreur lors de l'import-around: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@bp.get('/api/geocaches/<int:geocache_id>')
def get_geocache_details(geocache_id: int):
    """Récupère les détails complets d'une géocache."""
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        # Retourner le to_dict() complet qui inclut waypoints et checkers
        result = geocache.to_dict()

        logger.info(f"Returning details for geocache {geocache.gc_code} (id={geocache_id})")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error fetching geocache {geocache_id}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.get('/api/geocaches/by-code/<string:gc_code>')
def get_geocache_by_code(gc_code: str):
    """Récupère les détails complets d'une géocache via son GC code."""
    try:
        code = (gc_code or '').strip().upper()
        if not code:
            return jsonify({'error': 'Missing gc_code'}), 400

        zone_id_raw = request.args.get('zone_id')
        zone_id = int(zone_id_raw) if zone_id_raw is not None and str(zone_id_raw).strip() else None

        query = Geocache.query.filter(Geocache.gc_code == code)
        if zone_id is not None:
            query = query.filter(Geocache.zone_id == zone_id)

        matches = query.all()
        if not matches:
            return jsonify({'error': 'Geocache not found'}), 404

        if len(matches) > 1 and zone_id is None:
            return jsonify({'error': 'Multiple geocaches found for this gc_code. Provide zone_id.'}), 409

        geocache = matches[0]
        result = geocache.to_dict()
        logger.info(f"Returning details for geocache {geocache.gc_code} (id={geocache.id}) via gc_code")
        return jsonify(result)
    except ValueError:
        return jsonify({'error': 'Invalid zone_id'}), 400
    except Exception as e:
        logger.error(f"Error fetching geocache by gc_code {gc_code}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.get('/api/geocaches/<int:geocache_id>/nearby')
def get_nearby_geocaches(geocache_id: int):
    """Récupère les géocaches dans un rayon autour d'une géocache spécifique."""
    try:
        # Paramètres de requête
        radius_km = float(request.args.get('radius', 5.0))  # Rayon en km, défaut 5km

        # Récupérer la géocache centrale
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        if not geocache.latitude or not geocache.longitude:
            return jsonify({'error': 'Geocache has no coordinates'}), 400

        center_lat = geocache.latitude
        center_lon = geocache.longitude

        # Approximation simple de la distance :
        # 1 degré latitude ≈ 111 km
        # 1 degré longitude ≈ 111 * cos(lat) km
        import math
        lat_delta = radius_km / 111.0  # degrés
        lon_delta = radius_km / (111.0 * math.cos(math.radians(center_lat)))  # degrés

        # Requête pour les géocaches dans la bounding box approximative
        nearby_geocaches = Geocache.query.filter(
            Geocache.id != geocache_id,  # Exclure la géocache centrale
            Geocache.latitude.between(center_lat - lat_delta, center_lat + lat_delta),
            Geocache.longitude.between(center_lon - lon_delta, center_lon + lon_delta),
            Geocache.latitude.isnot(None),
            Geocache.longitude.isnot(None)
        ).all()

        # Filtrer plus précisément avec la distance exacte (formule de Haversine)
        def haversine_distance(lat1, lon1, lat2, lon2):
            """Calcule la distance en km entre deux points."""
            R = 6371  # Rayon de la Terre en km

            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)

            a = math.sin(dlat/2) * math.sin(dlat/2) + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2) * math.sin(dlon/2)
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

            return R * c

        # Filtrer les géocaches vraiment dans le rayon
        filtered_geocaches = []
        for gc in nearby_geocaches:
            distance = haversine_distance(center_lat, center_lon, gc.latitude, gc.longitude)
            if distance <= radius_km:
                filtered_geocaches.append({
                    'id': gc.id,
                    'gc_code': gc.gc_code,
                    'name': gc.name,
                    'cache_type': gc.type,
                    'difficulty': gc.difficulty,
                    'terrain': gc.terrain,
                    'latitude': gc.latitude,
                    'longitude': gc.longitude,
                    'found': gc.found or False,
                    'is_corrected': gc.is_corrected or False,
                    'distance_km': round(distance, 2),
                    'zone_id': gc.zone_id
                })

        logger.info(f"Found {len(filtered_geocaches)} geocaches within {radius_km}km of {geocache.gc_code}")
        return jsonify({
            'center_geocache': {
                'id': geocache.id,
                'gc_code': geocache.gc_code,
                'latitude': center_lat,
                'longitude': center_lon
            },
            'nearby_geocaches': filtered_geocaches,
            'radius_km': radius_km
        })

    except Exception as e:
        logger.error(f"Error fetching nearby geocaches for geocache {geocache_id}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/add')
def add_geocache():
    """Ajoute une nouvelle géocache à une zone."""
    try:
        data = request.get_json(silent=True) or {}
        code = (data.get('code') or '').strip().upper()
        zone_id = data.get('zone_id')
        
        if not code:
            return jsonify({'error': 'Missing required field: code'}), 400
        if not zone_id:
            return jsonify({'error': 'Missing required field: zone_id'}), 400
        
        logger.info(f"Adding geocache {code} to zone {zone_id}")
        
        # Utiliser l'importer existant
        importer = GeocacheImporter()
        geocache = importer.import_by_code(zone_id, code)
        
        logger.info(f"Successfully added geocache {code} (id={geocache.id})")
        
        return jsonify({
            'message': f'Geocache {code} added successfully',
            'id': geocache.id,
            'gc_code': geocache.gc_code,
            'name': geocache.name,
        }), 201
        
    except LookupError as e:
        error_msg = str(e)
        logger.warning(f"Lookup error adding geocache: {error_msg}")
        if 'zone_not_found' in error_msg:
            return jsonify({'error': 'Zone not found'}), 404
        elif 'gc_not_found' in error_msg:
            return jsonify({'error': 'Geocache not found on geocaching.com'}), 404
        elif 'gc_timeout' in error_msg:
            return jsonify({'error': 'Timeout fetching geocache data'}), 504
        return jsonify({'error': error_msg}), 400
        
    except ValueError as e:
        logger.warning(f"Validation error adding geocache: {e}")
        return jsonify({'error': str(e)}), 400
        
    except Exception as e:
        logger.error(f"Error adding geocache: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500


@bp.delete('/api/geocaches/<int:geocache_id>')
def delete_geocache(geocache_id: int):
    """Supprime une géocache."""
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        gc_code = geocache.gc_code
        logger.info(f"Deleting geocache {gc_code} (id={geocache_id})")
        
        ArchiveService.snapshot_before_delete(geocache)

        db.session.delete(geocache)
        db.session.commit()

        try:
            remove_geocache_dir(geocache_id)
        except Exception as e:
            logger.warning(f"Failed to cleanup stored images for geocache {geocache_id}: {e}")
        
        logger.info(f"Successfully deleted geocache {gc_code}")
        return jsonify({'message': f'Geocache {gc_code} deleted successfully'}), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting geocache {geocache_id}: {e}")
        return jsonify({'error': 'Failed to delete geocache'}), 500


@bp.post('/api/geocaches/<int:geocache_id>/refresh')
def refresh_geocache(geocache_id: int):
    """Rafraîchit les données d'une géocache depuis geocaching.com."""
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        gc_code = geocache.gc_code
        # zone_id = geocache.zone_id  # réservé pour évolutions futures
        
        logger.info(f"Refreshing geocache {gc_code} (id={geocache_id})")
        
        # Scraper les nouvelles données
        scraper = GeocachingScraper()
        s = scraper.scrape(gc_code)
        
        # Mettre à jour les champs (préserver les données utilisateur)
        geocache.name = s.name
        geocache.url = s.url
        geocache.type = s.type
        geocache.size = s.size
        geocache.owner = s.owner
        geocache.difficulty = s.difficulty
        geocache.terrain = s.terrain
        geocache.latitude = s.latitude
        geocache.longitude = s.longitude
        geocache.placed_at = s.placed_at
        geocache.status = s.status or 'active'
        
        # Mettre à jour les données enrichies
        geocache.coordinates_raw = getattr(s, 'coordinates_raw', None)
        geocache.is_corrected = getattr(s, 'is_corrected', None)
        geocache.original_latitude = getattr(s, 'original_latitude', None)
        geocache.original_longitude = getattr(s, 'original_longitude', None)
        geocache.original_coordinates_raw = getattr(s, 'original_coordinates_raw', None)
        geocache.description_html = getattr(s, 'description_html', None)
        geocache.hints = getattr(s, 'hints', None)
        geocache.attributes = getattr(s, 'attributes', None)
        geocache.favorites_count = getattr(s, 'favorites_count', None)
        geocache.logs_count = getattr(s, 'logs_count', None)
        geocache.images = getattr(s, 'images', None)

        ensure_images_v2_for_geocache(geocache)
        
        # Supprimer et recréer les waypoints et checkers
        from ..geocaches.models import GeocacheWaypoint, GeocacheChecker
        
        GeocacheWaypoint.query.filter_by(geocache_id=geocache_id).delete()
        GeocacheChecker.query.filter_by(geocache_id=geocache_id).delete()
        
        for w in getattr(s, 'waypoints', []) or []:
            db.session.add(GeocacheWaypoint(
                geocache_id=geocache.id,
                prefix=w.get('prefix'),
                lookup=w.get('lookup'),
                name=w.get('name'),
                type=w.get('type'),
                latitude=w.get('latitude'),
                longitude=w.get('longitude'),
                gc_coords=w.get('gc_coords'),
                note=w.get('note'),
            ))
        
        for c in getattr(s, 'checkers', []) or []:
            db.session.add(GeocacheChecker(
                geocache_id=geocache.id,
                name=c.get('name'),
                url=c.get('url'),
            ))
        
        db.session.commit()
        
        logger.info(f"Successfully refreshed geocache {gc_code}")
        return jsonify({
            'message': f'Geocache {gc_code} refreshed successfully',
            'id': geocache.id,
            'gc_code': geocache.gc_code,
            'name': geocache.name,
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error refreshing geocache {geocache_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to refresh geocache'}), 500


@bp.patch('/api/geocaches/<int:geocache_id>/move')
def move_geocache(geocache_id: int):
    """Déplace une géocache vers une autre zone."""
    try:
        data = request.get_json(silent=True) or {}
        target_zone_id = data.get('target_zone_id')

        if not target_zone_id:
            return jsonify({'error': 'Missing required field: target_zone_id'}), 400

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        # Vérifier que la zone cible existe
        from ..models import Zone
        target_zone = Zone.query.get(target_zone_id)
        if not target_zone:
            return jsonify({'error': 'Target zone not found'}), 404

        old_zone_id = geocache.zone_id
        gc_code = geocache.gc_code

        # Vérifier si la géocache existe déjà dans la zone cible
        existing_geocache = Geocache.query.filter_by(
            gc_code=gc_code,
            zone_id=target_zone_id
        ).first()

        if existing_geocache:
            # La géocache existe déjà dans la zone cible, on la supprime de la zone source
            logger.info(f"Geocache {gc_code} already exists in target zone {target_zone_id}, removing from source zone {old_zone_id}")
            db.session.delete(geocache)
            db.session.commit()

            return jsonify({
                'message': f'Geocache {gc_code} removed from source zone (already exists in target zone)',
                'id': geocache.id,
                'gc_code': geocache.gc_code,
                'old_zone_id': old_zone_id,
                'new_zone_id': target_zone_id,
                'already_exists': True,
            }), 200
        else:
            # Déplacement normal
            logger.info(f"Moving geocache {gc_code} from zone {old_zone_id} to zone {target_zone_id}")

            # Mettre à jour la zone
            geocache.zone_id = target_zone_id
            db.session.commit()

            logger.info(f"Successfully moved geocache {gc_code}")
            return jsonify({
                'message': f'Geocache {gc_code} moved successfully',
                'id': geocache.id,
                'gc_code': geocache.gc_code,
                'old_zone_id': old_zone_id,
                'new_zone_id': target_zone_id,
                'already_exists': False,
            }), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error moving geocache {geocache_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to move geocache'}), 500


@bp.post('/api/geocaches/<int:geocache_id>/copy')
def copy_geocache(geocache_id: int):
    """Copie une géocache vers une autre zone."""
    try:
        data = request.get_json(silent=True) or {}
        target_zone_id = data.get('target_zone_id')
        
        if not target_zone_id:
            return jsonify({'error': 'Missing required field: target_zone_id'}), 400
        
        # Récupérer la géocache source
        source_geocache = Geocache.query.get(geocache_id)
        if not source_geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        # Vérifier que la zone cible existe
        from ..models import Zone
        target_zone = Zone.query.get(target_zone_id)
        if not target_zone:
            return jsonify({'error': 'Target zone not found'}), 404
        
        # Vérifier si la géocache existe déjà dans la zone cible
        existing = Geocache.query.filter_by(
            gc_code=source_geocache.gc_code,
            zone_id=target_zone_id
        ).first()
        
        if existing:
            return jsonify({
                'error': f'La géocache {source_geocache.gc_code} existe déjà dans la zone cible'
            }), 400
        
        from ..geocaches.models import GeocacheWaypoint, GeocacheChecker
        
        logger.info(f"Copying geocache {source_geocache.gc_code} from zone {source_geocache.zone_id} to zone {target_zone_id}")
        
        # Créer une nouvelle géocache avec les mêmes données
        new_geocache = Geocache(
            gc_code=source_geocache.gc_code,
            name=source_geocache.name,
            url=source_geocache.url,
            type=source_geocache.type,
            size=source_geocache.size,
            owner=source_geocache.owner,
            difficulty=source_geocache.difficulty,
            terrain=source_geocache.terrain,
            latitude=source_geocache.latitude,
            longitude=source_geocache.longitude,
            placed_at=source_geocache.placed_at,
            status=source_geocache.status,
            coordinates_raw=source_geocache.coordinates_raw,
            is_corrected=source_geocache.is_corrected,
            original_latitude=source_geocache.original_latitude,
            original_longitude=source_geocache.original_longitude,
            original_coordinates_raw=source_geocache.original_coordinates_raw,
            description_html=source_geocache.description_html,
            hints=source_geocache.hints,
            attributes=source_geocache.attributes,
            favorites_count=source_geocache.favorites_count,
            logs_count=source_geocache.logs_count,
            images=source_geocache.images,
            found=source_geocache.found,
            found_date=source_geocache.found_date,
            zone_id=target_zone_id
        )
        
        db.session.add(new_geocache)
        db.session.flush()  # Pour obtenir l'ID de la nouvelle géocache
        
        # Copier les waypoints
        for waypoint in source_geocache.waypoints:
            new_waypoint = GeocacheWaypoint(
                geocache_id=new_geocache.id,
                prefix=waypoint.prefix,
                lookup=waypoint.lookup,
                name=waypoint.name,
                type=waypoint.type,
                latitude=waypoint.latitude,
                longitude=waypoint.longitude,
                gc_coords=waypoint.gc_coords,
                note=waypoint.note
            )
            db.session.add(new_waypoint)
        
        # Copier les checkers
        for checker in source_geocache.checkers:
            new_checker = GeocacheChecker(
                geocache_id=new_geocache.id,
                name=checker.name,
                url=checker.url
            )
            db.session.add(new_checker)
        
        db.session.commit()
        
        logger.info(f"Successfully copied geocache {source_geocache.gc_code} to zone {target_zone_id}")
        return jsonify({
            'message': f'Geocache {source_geocache.gc_code} copied successfully',
            'new_id': new_geocache.id,
            'gc_code': new_geocache.gc_code,
            'source_zone_id': source_geocache.zone_id,
            'target_zone_id': target_zone_id,
        }), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error copying geocache {geocache_id}: {e}", exc_info=True)
        return jsonify({'error': 'Failed to copy geocache'}), 500


@bp.post('/api/geocaches/import-gpx')
def import_gpx():
    """Importe des géocaches depuis un fichier GPX (Pocket Query) ou ZIP.

    Implémentation simplifiée: extrait les codes GC des fichiers GPX et
    utilise GeocacheImporter.import_by_code(zone_id, code) pour créer/mettre à jour
    les géocaches. Emet un flux JSON par lignes (progress streaming) compatible
    avec le frontend.
    """
    try:
        uploaded_file = request.files.get('gpxFile')
        zone_id_raw = request.form.get('zone_id')
        _update_existing = request.form.get('updateExisting') == 'on'  # réservé, non utilisé ici

        if not uploaded_file:
            return jsonify({'error': 'Aucun fichier sélectionné'}), 400
        if not zone_id_raw:
            return jsonify({'error': 'ID de zone manquant'}), 400

        try:
            zone_id = int(zone_id_raw)
        except ValueError:
            return jsonify({'error': 'ID de zone invalide'}), 400

        importer = GeocacheImporter()

        def extract_gc_codes_from_gpx_bytes(data: bytes) -> list[str]:
            codes: list[str] = []
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                return codes

            # Essayer GPX 1.0 et 1.1
            namespaces = [
                {'default': 'http://www.topografix.com/GPX/1/0'},
                {'default': 'http://www.topografix.com/GPX/1/1'},
                {},  # sans namespace (fallback)
            ]

            seen = set()
            for ns in namespaces:
                # wpt nodes
                if ns:
                    wpts = root.findall('default:wpt', ns)
                else:
                    wpts = root.findall('wpt')
                for w in wpts:
                    # name element
                    name_elem = w.find('default:name', ns) if ns else w.find('name')
                    if name_elem is None or not (name_elem.text or '').strip():
                        continue
                    waypoint_code = name_elem.text.strip()
                    # Garder uniquement les codes principaux commençant par GC et sans suffixe '-...'
                    if waypoint_code.startswith('GC') and '-' not in waypoint_code:
                        if waypoint_code not in seen:
                            seen.add(waypoint_code)
                            codes.append(waypoint_code)
            return codes

        # Lire le fichier entier AVANT le streaming pour éviter les fermetures
        _filename = (uploaded_file.filename or '').lower()
        _file_bytes = uploaded_file.read()

        def generate():
            try:
                yield json.dumps({'message': 'Analyse du fichier...', 'progress': 0}) + '\n'

                gc_codes: list[str] = []

                if _filename.endswith('.zip'):
                    with zipfile.ZipFile(io.BytesIO(_file_bytes), 'r') as zf:
                        members = [m for m in zf.namelist() if m.lower().endswith('.gpx')]
                        if not members:
                            yield json.dumps({'error': True, 'message': 'Aucun fichier GPX dans l\'archive ZIP'}) + '\n'
                            return
                        yield json.dumps({'message': f'{len(members)} fichier(s) GPX détecté(s) dans le ZIP', 'progress': 5}) + '\n'
                        for i, m in enumerate(members, start=1):
                            data = zf.read(m)
                            codes = extract_gc_codes_from_gpx_bytes(data)
                            gc_codes.extend(codes)
                            # Progression fixe pendant l'extraction (5%), pas dépendante du nombre de fichiers
                            yield json.dumps({'message': f'Lecture {m}: {len(codes)} code(s) GC', 'progress': 5}) + '\n'
                else:
                    codes = extract_gc_codes_from_gpx_bytes(_file_bytes)
                    gc_codes.extend(codes)
                    yield json.dumps({'message': f'{len(codes)} code(s) GC détecté(s)', 'progress': 5}) + '\n'

                # Dédupliquer
                gc_codes = list(dict.fromkeys(gc_codes))
                total = len(gc_codes)
                if total == 0:
                    yield json.dumps({'error': True, 'message': 'Aucun code GC détecté dans le fichier'}) + '\n'
                    return

                yield json.dumps({'message': f'Import de {total} géocache(s)...', 'progress': 10}) + '\n'

                success = 0
                errors = 0
                for idx, code in enumerate(gc_codes, start=1):
                    try:
                        importer.import_by_code(zone_id, code)
                        success += 1
                        msg = f'Importée: {code} ({idx}/{total})'
                    except Exception as e:
                        errors += 1
                        msg = f'Erreur {code}: {e}'
                    # Progression linéaire basée sur le nombre de geocaches traitées (10% à 100%)
                    pct = 10 + int(idx / total * 90)
                    yield json.dumps({'message': msg, 'progress': pct}) + '\n'

                summary = f'Importation terminée: {success} succès'
                if errors:
                    summary += f', {errors} erreurs'
                yield json.dumps({'progress': 100, 'message': summary, 'final_summary': True, 'stats': {'success': success, 'errors': errors, 'total': total}}) + '\n'
            except Exception as e:
                logger.error(f"Erreur import GPX: {e}", exc_info=True)
                yield json.dumps({'error': True, 'message': f'Erreur: {str(e)}'}) + '\n'

        return Response(stream_with_context(generate()), content_type='application/json')

    except Exception as e:
        logger.error(f"Erreur lors de l'import GPX: {e}")
        return jsonify({'error': str(e)}), 500


@bp.get('/api/geocaches/user-bookmark-lists')
def get_user_bookmark_lists():
    """Récupère les listes de favoris de l'utilisateur."""
    try:
        importer = GeocacheImporter()
        bookmark_importer = BookmarkListImporter(session=importer.scraper.session)
        
        lists = bookmark_importer.get_user_bookmark_lists()
        
        return jsonify({'lists': lists}), 200
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des listes de favoris: {e}")
        return jsonify({'error': str(e)}), 500


@bp.get('/api/geocaches/user-pocket-queries')
def get_user_pocket_queries():
    """Récupère les Pocket Queries de l'utilisateur."""
    try:
        importer = GeocacheImporter()
        pq_importer = PocketQueryImporter(session=importer.scraper.session)
        
        queries = pq_importer.get_user_pocket_queries()
        
        return jsonify({'queries': queries}), 200
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des Pocket Queries: {e}")
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/import-bookmark-list')
def import_bookmark_list():
    """Importe des géocaches depuis une liste de favoris (Bookmark List).
    
    Extrait les codes GC de la liste et les importe un par un.
    Emet un flux JSON par lignes (progress streaming) compatible avec le frontend.
    """
    try:
        data = request.get_json(silent=True) or {}
        bookmark_code = data.get('bookmark_code', '').strip()
        zone_id_raw = data.get('zone_id')
        
        if not bookmark_code:
            return jsonify({'error': 'Code de liste de favoris manquant'}), 400
        if not zone_id_raw:
            return jsonify({'error': 'ID de zone manquant'}), 400
        
        try:
            zone_id = int(zone_id_raw)
        except ValueError:
            return jsonify({'error': 'ID de zone invalide'}), 400
        
        importer = GeocacheImporter()
        bookmark_importer = BookmarkListImporter(session=importer.scraper.session)
        
        def generate():
            try:
                yield json.dumps({'message': f'Récupération de la liste {bookmark_code}...', 'progress': 0}) + '\n'
                
                # Get list info
                try:
                    list_info = bookmark_importer.get_list_info(bookmark_code)
                    yield json.dumps({'message': f'Liste: {list_info.get("name", bookmark_code)}', 'progress': 5}) + '\n'
                except Exception as e:
                    logger.warning(f"Could not get list info: {e}")
                
                # Get geocache codes from the list
                try:
                    gc_codes = bookmark_importer.get_geocaches_from_list(bookmark_code)
                except LookupError as e:
                    error_msg = str(e)
                    if 'not_found' in error_msg:
                        yield json.dumps({'error': True, 'message': 'Liste de favoris introuvable'}) + '\n'
                    elif 'private' in error_msg:
                        yield json.dumps({'error': True, 'message': 'Liste de favoris privée ou authentification requise'}) + '\n'
                    elif 'no_geocaches' in error_msg:
                        yield json.dumps({'error': True, 'message': 'Aucune géocache trouvée dans cette liste'}) + '\n'
                    else:
                        yield json.dumps({'error': True, 'message': f'Erreur: {error_msg}'}) + '\n'
                    return
                except Exception as e:
                    yield json.dumps({'error': True, 'message': f'Erreur lors de la récupération de la liste: {str(e)}'}) + '\n'
                    return
                
                total = len(gc_codes)
                yield json.dumps({'message': f'{total} géocache(s) trouvée(s) dans la liste', 'progress': 10}) + '\n'
                
                success = 0
                errors = 0
                for idx, code in enumerate(gc_codes, start=1):
                    try:
                        importer.import_by_code(zone_id, code)
                        success += 1
                        msg = f'Importée: {code} ({idx}/{total})'
                    except Exception as e:
                        errors += 1
                        msg = f'Erreur {code}: {e}'
                    
                    pct = 10 + int(idx / total * 90)
                    yield json.dumps({'message': msg, 'progress': pct}) + '\n'
                
                summary = f'Importation terminée: {success} succès'
                if errors:
                    summary += f', {errors} erreurs'
                yield json.dumps({
                    'progress': 100,
                    'message': summary,
                    'final_summary': True,
                    'stats': {'success': success, 'errors': errors, 'total': total}
                }) + '\n'
                
            except Exception as e:
                logger.error(f"Erreur import bookmark list: {e}", exc_info=True)
                yield json.dumps({'error': True, 'message': f'Erreur: {str(e)}'}) + '\n'
        
        return Response(stream_with_context(generate()), content_type='application/json')
    
    except Exception as e:
        logger.error(f"Erreur lors de l'import de la liste de favoris: {e}")
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/import-pocket-query')
def import_pocket_query():
    """Importe des géocaches depuis une Pocket Query.
    
    Télécharge le fichier GPX/ZIP de la Pocket Query et l'importe.
    Emet un flux JSON par lignes (progress streaming) compatible avec le frontend.
    """
    try:
        data = request.get_json(silent=True) or {}
        pq_code = data.get('pq_code', '').strip()
        zone_id_raw = data.get('zone_id')
        
        if not pq_code:
            return jsonify({'error': 'Code de Pocket Query manquant'}), 400
        if not zone_id_raw:
            return jsonify({'error': 'ID de zone manquant'}), 400
        
        try:
            zone_id = int(zone_id_raw)
        except ValueError:
            return jsonify({'error': 'ID de zone invalide'}), 400
        
        importer = GeocacheImporter()
        pq_importer = PocketQueryImporter(session=importer.scraper.session)
        
        def extract_gc_codes_from_gpx_bytes(data: bytes) -> list[str]:
            codes: list[str] = []
            try:
                root = ET.fromstring(data)
            except ET.ParseError:
                return codes
            
            namespaces = [
                {'default': 'http://www.topografix.com/GPX/1/0'},
                {'default': 'http://www.topografix.com/GPX/1/1'},
                {},
            ]
            
            seen = set()
            for ns in namespaces:
                if ns:
                    wpts = root.findall('default:wpt', ns)
                else:
                    wpts = root.findall('wpt')
                for w in wpts:
                    name_elem = w.find('default:name', ns) if ns else w.find('name')
                    if name_elem is None or not (name_elem.text or '').strip():
                        continue
                    waypoint_code = name_elem.text.strip()
                    if waypoint_code.startswith('GC') and '-' not in waypoint_code:
                        if waypoint_code not in seen:
                            seen.add(waypoint_code)
                            codes.append(waypoint_code)
            return codes
        
        def generate():
            try:
                yield json.dumps({'message': f'Téléchargement de la Pocket Query {pq_code}...', 'progress': 0}) + '\n'
                
                # Download the pocket query
                try:
                    file_bytes = pq_importer.download_pocket_query_gpx(pq_code)
                    yield json.dumps({'message': f'Fichier téléchargé ({len(file_bytes)} octets)', 'progress': 5}) + '\n'
                except LookupError as e:
                    error_msg = str(e)
                    if 'not_found' in error_msg:
                        yield json.dumps({'error': True, 'message': 'Pocket Query introuvable'}) + '\n'
                    elif 'premium' in error_msg:
                        yield json.dumps({'error': True, 'message': 'Pocket Query nécessite un compte Premium ou une authentification'}) + '\n'
                    else:
                        yield json.dumps({'error': True, 'message': f'Erreur: {error_msg}'}) + '\n'
                    return
                except Exception as e:
                    yield json.dumps({'error': True, 'message': f'Erreur lors du téléchargement: {str(e)}'}) + '\n'
                    return
                
                # Extract GC codes from the downloaded file
                yield json.dumps({'message': 'Analyse du fichier...', 'progress': 10}) + '\n'
                
                gc_codes: list[str] = []
                
                # Check if it's a ZIP file
                if file_bytes[:2] == b'PK':
                    try:
                        with zipfile.ZipFile(io.BytesIO(file_bytes), 'r') as zf:
                            members = [m for m in zf.namelist() if m.lower().endswith('.gpx')]
                            if not members:
                                yield json.dumps({'error': True, 'message': 'Aucun fichier GPX dans l\'archive ZIP'}) + '\n'
                                return
                            yield json.dumps({'message': f'{len(members)} fichier(s) GPX détecté(s)', 'progress': 15}) + '\n'
                            for m in members:
                                data = zf.read(m)
                                codes = extract_gc_codes_from_gpx_bytes(data)
                                gc_codes.extend(codes)
                    except Exception as e:
                        yield json.dumps({'error': True, 'message': f'Erreur lors de la lecture du ZIP: {str(e)}'}) + '\n'
                        return
                else:
                    # Assume it's a GPX file
                    codes = extract_gc_codes_from_gpx_bytes(file_bytes)
                    gc_codes.extend(codes)
                
                # Deduplicate
                gc_codes = list(dict.fromkeys(gc_codes))
                total = len(gc_codes)
                
                if total == 0:
                    yield json.dumps({'error': True, 'message': 'Aucun code GC détecté dans le fichier'}) + '\n'
                    return
                
                yield json.dumps({'message': f'{total} géocache(s) trouvée(s)', 'progress': 20}) + '\n'
                
                success = 0
                errors = 0
                for idx, code in enumerate(gc_codes, start=1):
                    try:
                        importer.import_by_code(zone_id, code)
                        success += 1
                        msg = f'Importée: {code} ({idx}/{total})'
                    except Exception as e:
                        errors += 1
                        msg = f'Erreur {code}: {e}'
                    
                    pct = 20 + int(idx / total * 80)
                    yield json.dumps({'message': msg, 'progress': pct}) + '\n'
                
                summary = f'Importation terminée: {success} succès'
                if errors:
                    summary += f', {errors} erreurs'
                yield json.dumps({
                    'progress': 100,
                    'message': summary,
                    'final_summary': True,
                    'stats': {'success': success, 'errors': errors, 'total': total}
                }) + '\n'
                
            except Exception as e:
                logger.error(f"Erreur import pocket query: {e}", exc_info=True)
                yield json.dumps({'error': True, 'message': f'Erreur: {str(e)}'}) + '\n'
        
        return Response(stream_with_context(generate()), content_type='application/json')
    
    except Exception as e:
        logger.error(f"Erreur lors de l'import de la Pocket Query: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINTS CRUD POUR LES WAYPOINTS
# ============================================================================

@bp.post('/api/geocaches/<int:geocache_id>/waypoints')
def create_waypoint(geocache_id: int):
    """Crée un nouveau waypoint pour une géocache."""
    try:
        from ..geocaches.models import GeocacheWaypoint
        
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        data = request.get_json()
        
        # 🔍 LOG DÉTAILLÉ : Données reçues
        logger.info(f"[CREATE WAYPOINT] Geocache {geocache_id}")
        logger.info(f"[CREATE WAYPOINT] Données reçues: {data}")
        logger.info(f"[CREATE WAYPOINT] gc_coords reçu: {data.get('gc_coords')}")
        
        # ✅ PARSER les coordonnées GC pour calculer lat/lon
        latitude = None
        longitude = None
        gc_coords = data.get('gc_coords')
        
        if gc_coords:
            import re
            # Gérer avec ou sans virgule : "N 48° 38.204, E 006° 07.000" OU "N 48° 38.204 E 006° 07.000"
            parts = gc_coords.split(',') if ',' in gc_coords else None
            if not parts or len(parts) != 2:
                # Pas de virgule, séparer par regex
                match = re.match(r'^([NS][^EW]+)\s+([EW].+)$', gc_coords)
                if match:
                    parts = [match.group(1), match.group(2)]
            
            if parts and len(parts) == 2:
                lat_str = parts[0].strip()
                lon_str = parts[1].strip()
                
                # Parser latitude
                lat_match = re.match(r'([NS])\s*(\d+)°\s*([\d.]+)', lat_str)
                lon_match = re.match(r'([EW])\s*(\d+)°\s*([\d.]+)', lon_str)
                
                if lat_match and lon_match:
                    lat = (int(lat_match.group(2)) + float(lat_match.group(3)) / 60)
                    if lat_match.group(1) == 'S':
                        lat = -lat
                    
                    lon = (int(lon_match.group(2)) + float(lon_match.group(3)) / 60)
                    if lon_match.group(1) == 'W':
                        lon = -lon
                    
                    latitude = lat
                    longitude = lon
                    logger.info(f"[CREATE WAYPOINT] ✅ Coordonnées parsées: {gc_coords} → lat={latitude}, lon={longitude}")
                else:
                    logger.warning(f"[CREATE WAYPOINT] ⚠️ Impossible de parser: {gc_coords}")
        
        waypoint = GeocacheWaypoint(
            geocache_id=geocache_id,
            prefix=data.get('prefix'),
            lookup=data.get('lookup'),
            name=data.get('name'),
            type=data.get('type'),
            latitude=latitude,  # ✅ Coordonnées parsées par le backend
            longitude=longitude,  # ✅ Coordonnées parsées par le backend
            gc_coords=gc_coords,
            note=data.get('note'),
            note_override=(data.get('note_override') if data.get('note_override') is not None else data.get('note'))
        )
        
        # 🔍 LOG DÉTAILLÉ : Avant commit
        logger.info(f"[CREATE WAYPOINT] Avant commit - waypoint.gc_coords: {waypoint.gc_coords}")
        logger.info(f"[CREATE WAYPOINT] Avant commit - waypoint.latitude: {waypoint.latitude}")
        logger.info(f"[CREATE WAYPOINT] Avant commit - waypoint.longitude: {waypoint.longitude}")
        
        db.session.add(waypoint)
        db.session.commit()
        
        # 🔍 LOG DÉTAILLÉ : Après commit
        logger.info(f"[CREATE WAYPOINT] Après commit - ID: {waypoint.id}")
        logger.info(f"[CREATE WAYPOINT] Après commit - waypoint.gc_coords: {waypoint.gc_coords}")
        logger.info(f"[CREATE WAYPOINT] Après commit - waypoint.latitude: {waypoint.latitude}")
        logger.info(f"[CREATE WAYPOINT] Après commit - waypoint.longitude: {waypoint.longitude}")

        if (geocache.solved or 'not_solved') in ('in_progress', 'solved') or geocache.is_corrected:
            ArchiveService.sync_from_geocache(geocache)

        return jsonify(waypoint.to_dict()), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating waypoint: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.put('/api/geocaches/<int:geocache_id>/waypoints/<int:waypoint_id>')
def update_waypoint(geocache_id: int, waypoint_id: int):
    """Met à jour un waypoint existant."""
    try:
        from ..geocaches.models import GeocacheWaypoint
        
        waypoint = GeocacheWaypoint.query.filter_by(
            id=waypoint_id,
            geocache_id=geocache_id
        ).first()
        
        if not waypoint:
            return jsonify({'error': 'Waypoint not found'}), 404
        
        data = request.get_json()
        
        # 🔍 LOG DÉTAILLÉ : Données reçues
        logger.info(f"[UPDATE WAYPOINT] Waypoint {waypoint_id} - Geocache {geocache_id}")
        logger.info(f"[UPDATE WAYPOINT] Données reçues: {data}")
        logger.info(f"[UPDATE WAYPOINT] gc_coords reçu: {data.get('gc_coords')}")
        
        # 🔍 LOG DÉTAILLÉ : Avant modification
        logger.info(f"[UPDATE WAYPOINT] Avant - waypoint.gc_coords: {waypoint.gc_coords}")
        logger.info(f"[UPDATE WAYPOINT] Avant - waypoint.latitude: {waypoint.latitude}")
        logger.info(f"[UPDATE WAYPOINT] Avant - waypoint.longitude: {waypoint.longitude}")
        
        waypoint.prefix = data.get('prefix', waypoint.prefix)
        waypoint.lookup = data.get('lookup', waypoint.lookup)
        waypoint.name = data.get('name', waypoint.name)
        waypoint.type = data.get('type', waypoint.type)
        note_override = data.get('note_override')
        if note_override is None and 'note' in data:
            note_override = data.get('note')
        if note_override is not None:
            waypoint.note_override = str(note_override)
            waypoint.note_override_updated_at = datetime.now(timezone.utc)
        
        # ✅ Si gc_coords a changé, recalculer lat/lon
        new_gc_coords = data.get('gc_coords')
        if new_gc_coords and new_gc_coords != waypoint.gc_coords:
            import re
            # Gérer avec ou sans virgule
            parts = new_gc_coords.split(',') if ',' in new_gc_coords else None
            if not parts or len(parts) != 2:
                match = re.match(r'^([NS][^EW]+)\s+([EW].+)$', new_gc_coords)
                if match:
                    parts = [match.group(1), match.group(2)]
            
            if parts and len(parts) == 2:
                lat_str = parts[0].strip()
                lon_str = parts[1].strip()
                
                lat_match = re.match(r'([NS])\s*(\d+)°\s*([\d.]+)', lat_str)
                lon_match = re.match(r'([EW])\s*(\d+)°\s*([\d.]+)', lon_str)
                
                if lat_match and lon_match:
                    lat = (int(lat_match.group(2)) + float(lat_match.group(3)) / 60)
                    if lat_match.group(1) == 'S':
                        lat = -lat
                    
                    lon = (int(lon_match.group(2)) + float(lon_match.group(3)) / 60)
                    if lon_match.group(1) == 'W':
                        lon = -lon
                    
                    waypoint.latitude = lat
                    waypoint.longitude = lon
                    logger.info(f"[UPDATE WAYPOINT] ✅ Coordonnées parsées: {new_gc_coords} → lat={lat}, lon={lon}")
                else:
                    logger.warning(f"[UPDATE WAYPOINT] ⚠️ Impossible de parser: {new_gc_coords}")
            
            waypoint.gc_coords = new_gc_coords
        
        # 🔍 LOG DÉTAILLÉ : Après modification, avant commit
        logger.info(f"[UPDATE WAYPOINT] Après modif - waypoint.gc_coords: {waypoint.gc_coords}")
        logger.info(f"[UPDATE WAYPOINT] Après modif - waypoint.latitude: {waypoint.latitude}")
        logger.info(f"[UPDATE WAYPOINT] Après modif - waypoint.longitude: {waypoint.longitude}")
        
        db.session.commit()
        
        # 🔍 LOG DÉTAILLÉ : Après commit
        logger.info(f"[UPDATE WAYPOINT] Après commit - waypoint.gc_coords: {waypoint.gc_coords}")
        logger.info(f"[UPDATE WAYPOINT] Après commit - waypoint.latitude: {waypoint.latitude}")
        logger.info(f"[UPDATE WAYPOINT] Après commit - waypoint.longitude: {waypoint.longitude}")

        geocache_for_archive = Geocache.query.get(geocache_id)
        if geocache_for_archive and (geocache_for_archive.solved or 'not_solved') in ('in_progress', 'solved'):
            ArchiveService.sync_from_geocache(geocache_for_archive)

        return jsonify(waypoint.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erreur lors de la mise à jour du waypoint: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.put('/api/geocaches/<int:geocache_id>/translated-content')
def update_translated_content(geocache_id: int):
    try:
        from ..geocaches.models import GeocacheWaypoint

        data = request.get_json() or {}

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        any_update = False

        override_raw = data.get('description_override_raw')
        override_html = data.get('description_override_html')
        if override_raw is not None or override_html is not None:
            if override_raw is not None:
                geocache.description_override_raw = str(override_raw)
            if override_html is not None:
                geocache.description_override_html = str(override_html)
            geocache.description_override_updated_at = datetime.now(timezone.utc)
            any_update = True

        hints_decoded_override = data.get('hints_decoded_override')
        if hints_decoded_override is not None:
            geocache.hints_decoded_override = str(hints_decoded_override)
            geocache.hints_decoded_override_updated_at = datetime.now(timezone.utc)
            any_update = True

        waypoints = data.get('waypoints')
        if isinstance(waypoints, list):
            for item in waypoints:
                if not isinstance(item, dict):
                    continue
                waypoint_id = item.get('id')
                note_override = item.get('note_override')
                if waypoint_id is None or note_override is None:
                    continue

                waypoint = GeocacheWaypoint.query.filter_by(id=int(waypoint_id), geocache_id=geocache_id).first()
                if not waypoint:
                    continue

                waypoint.note_override = str(note_override)
                waypoint.note_override_updated_at = datetime.now(timezone.utc)
                any_update = True

        if not any_update:
            return jsonify({'error': 'No updates provided'}), 400

        db.session.commit()
        return jsonify({'success': True, 'geocache': geocache.to_dict()})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating translated content: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/<int:geocache_id>/set-corrected-coords/<int:waypoint_id>')
def set_corrected_coords_from_waypoint(geocache_id: int, waypoint_id: int):
    """Définit les coordonnées d'un waypoint comme coordonnées corrigées de la géocache."""
    try:
        from ..geocaches.models import Geocache, GeocacheWaypoint
        
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        waypoint = GeocacheWaypoint.query.filter_by(
            id=waypoint_id,
            geocache_id=geocache_id
        ).first()
        
        if not waypoint:
            return jsonify({'error': 'Waypoint not found'}), 404
        
        if not waypoint.latitude or not waypoint.longitude:
            return jsonify({'error': 'Waypoint has no coordinates'}), 400
        
        if not waypoint.gc_coords:
            return jsonify({'error': 'Waypoint has no gc_coords'}), 400
        
        logger.info(f"[SET CORRECTED COORDS] Geocache {geocache_id} - Waypoint {waypoint_id}")
        logger.info(f"[SET CORRECTED COORDS] Anciennes coords: {geocache.coordinates_raw} (lat={geocache.latitude}, lon={geocache.longitude})")
        logger.info(f"[SET CORRECTED COORDS] Nouvelles coords: {waypoint.gc_coords} (lat={waypoint.latitude}, lon={waypoint.longitude})")
        
        # Sauvegarder les coordonnées originales si ce n'est pas déjà fait
        if not geocache.is_corrected:
            geocache.original_latitude = geocache.latitude
            geocache.original_longitude = geocache.longitude
            geocache.original_coordinates_raw = geocache.coordinates_raw
            logger.info(f"[SET CORRECTED COORDS] Sauvegarde des coordonnées originales: {geocache.original_coordinates_raw}")
        
        # Mettre à jour avec les coordonnées du waypoint (format raw + décimales)
        geocache.coordinates_raw = waypoint.gc_coords
        geocache.latitude = waypoint.latitude
        geocache.longitude = waypoint.longitude
        geocache.is_corrected = True
        
        db.session.commit()
        
        logger.info("[SET CORRECTED COORDS] Coordonnées corrigées mises à jour")
        
        return jsonify({
            'success': True,
            'geocache': geocache.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erreur lors de la mise à jour des coordonnées corrigées: {str(e)}")
        return jsonify({'error': str(e)}), 500


@bp.delete('/api/geocaches/<int:geocache_id>/waypoints/<int:waypoint_id>')
def delete_waypoint(geocache_id: int, waypoint_id: int):
    """Supprime un waypoint."""
    try:
        from ..geocaches.models import GeocacheWaypoint
        
        waypoint = GeocacheWaypoint.query.filter_by(
            id=waypoint_id,
            geocache_id=geocache_id
        ).first()
        
        if not waypoint:
            return jsonify({'error': 'Waypoint not found'}), 404
        
        db.session.delete(waypoint)
        db.session.commit()
        
        logger.info(f"Deleted waypoint {waypoint_id} for geocache {geocache_id}")
        return jsonify({'success': True}), 200
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting waypoint: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.put('/api/geocaches/<int:geocache_id>/coordinates')
def update_coordinates(geocache_id: int):
    """
    Met à jour les coordonnées d'une géocache.
    Permet de corriger les coordonnées en conservant les originales.
    """
    try:
        data = request.get_json()
        coordinates_raw = data.get('coordinates_raw')
        
        if not coordinates_raw:
            return jsonify({'error': 'coordinates_raw required'}), 400
        
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        # Parse les coordonnées pour calculer lat/lon
        def parse_gc_coordinates(coords_text: str):
            try:
                # Nettoyer les apostrophes qui peuvent rester
                coords_text = coords_text.replace("'", "").replace("'", "").replace("ʼ", "").replace("′", "")
                logger.info(f"Parsing coordinates: '{coords_text}'")
                parts = coords_text.split()
                logger.info(f"Parts: {parts}")
                if len(parts) < 6:
                    logger.warning(f"Not enough parts: {len(parts)}")
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
        
        lat, lon = parse_gc_coordinates(coordinates_raw)
        if lat is None or lon is None:
            return jsonify({'error': 'Invalid coordinates format'}), 400
        
        # Mettre à jour les coordonnées
        geocache.coordinates_raw = coordinates_raw
        geocache.latitude = lat
        geocache.longitude = lon
        geocache.is_corrected = True
        
        db.session.commit()

        ArchiveService.sync_from_geocache(geocache)
        
        logger.info(f"Updated coordinates for geocache {geocache_id}")
        return jsonify({'success': True, 'geocache': geocache.to_dict()})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating coordinates: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.put('/api/geocaches/<int:geocache_id>/description')
def update_description(geocache_id: int):
    try:
        data = request.get_json() or {}
        override_raw = data.get('description_override_raw')
        override_html = data.get('description_override_html')

        if override_raw is None and override_html is None:
            return jsonify({'error': 'description_override_raw or description_override_html required'}), 400

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        # Store raw text as the primary edited variant.
        if override_raw is not None:
            geocache.description_override_raw = str(override_raw)

        # HTML is optional (frontend can send it, or keep it unset).
        if override_html is not None:
            geocache.description_override_html = str(override_html)

        geocache.description_override_updated_at = datetime.now(timezone.utc)

        db.session.commit()

        logger.info(f"Updated description override for geocache {geocache_id}")
        return jsonify({'success': True, 'geocache': geocache.to_dict()})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating description override: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/<int:geocache_id>/reset-coordinates')
def reset_coordinates(geocache_id: int):
    """
    Réinitialise les coordonnées aux valeurs originales.
    """
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        # Restaurer les coordonnées originales
        if geocache.original_coordinates_raw:
            geocache.coordinates_raw = geocache.original_coordinates_raw
        if geocache.original_latitude is not None:
            geocache.latitude = geocache.original_latitude
        if geocache.original_longitude is not None:
            geocache.longitude = geocache.original_longitude
        geocache.is_corrected = False
        
        db.session.commit()

        ArchiveService.sync_from_geocache(geocache)
        
        logger.info(f"Reset coordinates for geocache {geocache_id}")
        return jsonify({'success': True, 'geocache': geocache.to_dict()})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error resetting coordinates: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/<int:geocache_id>/reset-description')
def reset_description(geocache_id: int):
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        geocache.description_override_raw = None
        geocache.description_override_html = None
        geocache.description_override_updated_at = None

        db.session.commit()

        logger.info(f"Reset description override for geocache {geocache_id}")
        return jsonify({'success': True, 'geocache': geocache.to_dict()})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error resetting description override: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/<int:geocache_id>/push-corrected-coordinates')
def push_corrected_coordinates(geocache_id: int):
    """
    Envoie les coordonnées corrigées de la géocache vers Geocaching.com.

    La géocache doit avoir des coordonnées corrigées (is_corrected=True).
    Utilise le service d'authentification centralisé (GeocachingAuthService).

    Returns:
        200 {'success': True, 'message': '...'}
        400 si pas de coordonnées corrigées ou code GC manquant
        401 si non connecté à Geocaching.com
        500 en cas d'erreur réseau ou API
    """
    try:
        from ..services.geocaching_push_coordinates import GeocachingPushCoordinatesClient
        from ..services.geocaching_auth import get_auth_service

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        if not geocache.gc_code:
            return jsonify({'error': 'Code GC manquant pour cette géocache'}), 400

        if not geocache.is_corrected or geocache.latitude is None or geocache.longitude is None:
            return jsonify({'error': 'Aucune coordonnée corrigée disponible pour cette géocache'}), 400

        auth_service = get_auth_service()
        if not auth_service.is_logged_in():
            return jsonify({'error': 'Non connecté à Geocaching.com — configurez l\'authentification'}), 401

        client = GeocachingPushCoordinatesClient()
        result = client.push_corrected_coordinates(
            gc_code=geocache.gc_code,
            latitude=geocache.latitude,
            longitude=geocache.longitude,
        )

        if not result.get('ok'):
            status_code = result.get('status', 500)
            error_msg = result.get('error') or result.get('body') or 'Erreur inconnue'
            logger.error(f"Push corrected coords failed for {geocache.gc_code}: {error_msg}")
            if status_code in (401, 403):
                return jsonify({'error': error_msg}), 401
            return jsonify({'error': error_msg}), 502

        logger.info(f"Corrected coordinates pushed to Geocaching.com for {geocache.gc_code}")
        return jsonify({
            'success': True,
            'message': f'Coordonnées corrigées envoyées vers Geocaching.com ({geocache.gc_code})',
            'gc_code': geocache.gc_code,
            'latitude': geocache.latitude,
            'longitude': geocache.longitude,
            'coordinates_raw': geocache.coordinates_raw,
        })

    except Exception as e:
        logger.error(f"Error pushing corrected coordinates for geocache {geocache_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/<int:geocache_id>/waypoints/<int:waypoint_id>/push-coordinates')
def push_waypoint_coordinates(geocache_id: int, waypoint_id: int):
    """
    Envoie les coordonnées d'un waypoint comme coordonnées corrigées vers Geocaching.com.

    Utile pour définir la position finale d'une mystery cache depuis un waypoint résolu.

    Returns:
        200 {'success': True, 'message': '...'}
        400 si coordonnées manquantes
        401 si non connecté
        404 si geocache ou waypoint introuvable
        502 si l'API Geocaching.com renvoie une erreur
    """
    try:
        from ..services.geocaching_push_coordinates import GeocachingPushCoordinatesClient
        from ..services.geocaching_auth import get_auth_service
        from ..geocaches.models import GeocacheWaypoint

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        if not geocache.gc_code:
            return jsonify({'error': 'Code GC manquant pour cette géocache'}), 400

        waypoint = GeocacheWaypoint.query.filter_by(id=waypoint_id, geocache_id=geocache_id).first()
        if not waypoint:
            return jsonify({'error': 'Waypoint not found'}), 404

        if waypoint.latitude is None or waypoint.longitude is None:
            return jsonify({'error': 'Ce waypoint n\'a pas de coordonnées valides'}), 400

        auth_service = get_auth_service()
        if not auth_service.is_logged_in():
            return jsonify({'error': 'Non connecté à Geocaching.com — configurez l\'authentification'}), 401

        client = GeocachingPushCoordinatesClient()
        result = client.push_corrected_coordinates(
            gc_code=geocache.gc_code,
            latitude=waypoint.latitude,
            longitude=waypoint.longitude,
        )

        if not result.get('ok'):
            error_msg = result.get('error') or result.get('body') or 'Erreur inconnue'
            logger.error(f"Push waypoint coords failed for {geocache.gc_code} wp={waypoint_id}: {error_msg}")
            status_code = result.get('status', 500)
            if status_code in (401, 403):
                return jsonify({'error': error_msg}), 401
            return jsonify({'error': error_msg}), 502

        coords_display = waypoint.gc_coords or f"{waypoint.latitude:.6f}, {waypoint.longitude:.6f}"
        logger.info(f"Waypoint {waypoint_id} coordinates pushed to Geocaching.com for {geocache.gc_code}")
        return jsonify({
            'success': True,
            'message': f'Coordonnées du waypoint envoyées vers Geocaching.com ({geocache.gc_code})',
            'gc_code': geocache.gc_code,
            'waypoint_id': waypoint_id,
            'waypoint_name': waypoint.name,
            'latitude': waypoint.latitude,
            'longitude': waypoint.longitude,
            'coordinates_display': coords_display,
        })

    except Exception as e:
        logger.error(f"Error pushing waypoint coordinates for geocache {geocache_id} wp={waypoint_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.delete('/api/geocaches/<int:geocache_id>/push-corrected-coordinates')
def delete_corrected_coordinates_on_gc(geocache_id: int):
    """
    Supprime les coordonnées corrigées sur Geocaching.com (remet les coords originales).

    Returns:
        200 {'success': True}
        401 si non connecté
        404 si geocache introuvable
        502 si l'API Geocaching.com renvoie une erreur
    """
    try:
        from ..services.geocaching_push_coordinates import GeocachingPushCoordinatesClient
        from ..services.geocaching_auth import get_auth_service

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        if not geocache.gc_code:
            return jsonify({'error': 'Code GC manquant pour cette géocache'}), 400

        auth_service = get_auth_service()
        if not auth_service.is_logged_in():
            return jsonify({'error': 'Non connecté à Geocaching.com — configurez l\'authentification'}), 401

        client = GeocachingPushCoordinatesClient()
        result = client.delete_corrected_coordinates(gc_code=geocache.gc_code)

        if not result.get('ok'):
            error_msg = result.get('error') or result.get('body') or 'Erreur inconnue'
            logger.error(f"Delete corrected coords failed for {geocache.gc_code}: {error_msg}")
            status_code = result.get('status', 500)
            if status_code in (401, 403):
                return jsonify({'error': error_msg}), 401
            return jsonify({'error': error_msg}), 502

        logger.info(f"Corrected coordinates deleted on Geocaching.com for {geocache.gc_code}")
        return jsonify({
            'success': True,
            'message': f'Coordonnées corrigées supprimées sur Geocaching.com ({geocache.gc_code})',
        })

    except Exception as e:
        logger.error(f"Error deleting corrected coordinates for geocache {geocache_id}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.put('/api/geocaches/<int:geocache_id>/solved-status')
def update_solved_status(geocache_id: int):
    """
    Met à jour le statut de résolution d'une géocache.
    """
    try:
        data = request.get_json()
        solved_status = data.get('solved_status')

        if solved_status not in ['not_solved', 'in_progress', 'solved']:
            return jsonify({'error': 'Invalid solved_status'}), 400

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        geocache.solved = solved_status
        db.session.commit()

        ArchiveService.sync_from_geocache(geocache)

        logger.info(f"Updated solved status for geocache {geocache_id} to {solved_status}")
        return jsonify({'success': True, 'solved': solved_status})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating solved status: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500