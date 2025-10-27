from __future__ import annotations

import logging
from flask import Blueprint, jsonify, request

from ..database import db
from ..models import Zone
from ..geocaches.models import Geocache
from ..geocaches.importer import GeocacheImporter


logger = logging.getLogger(__name__)


bp = Blueprint('geocaches', __name__)


@bp.post('/api/geocaches/import')
def import_geocache():
    logger.info("POST /api/geocaches/import called")

    try:
        data = request.get_json(silent=True) or {}
        zone_id = data.get('zone_id')
        gc_code = (data.get('gc_code') or '').strip().upper()

        logger.info(f"Import request: zone_id={zone_id}, gc_code='{gc_code}'")

        if not zone_id or not isinstance(zone_id, int):
            logger.warning("Missing or invalid zone_id")
            return jsonify({'error': 'zone_id requis (entier)'}), 400
        if not gc_code:
            logger.warning("Missing gc_code")
            return jsonify({'error': 'gc_code requis'}), 400

        importer = GeocacheImporter()
        g = importer.import_by_code(zone_id, gc_code)

        logger.info(f"Import successful: geocache {g.gc_code} (id={g.id})")
        return jsonify(g.to_dict()), 200

    except ValueError as ve:
        logger.warning(f"ValueError during import: {ve}")
        if str(ve) == 'invalid_gc_code':
            return jsonify({'error': 'gc_code invalide (format GCXXXXX)'}), 400
        return jsonify({'error': 'bad_request', 'detail': str(ve)}), 400
    except LookupError as le:
        logger.warning(f"LookupError during import: {le}")
        if str(le) == 'zone_not_found':
            return jsonify({'error': 'zone introuvable'}), 404
        if str(le) == 'gc_not_found':
            return jsonify({'error': 'geocache introuvable sur Geocaching.com'}), 404
        if str(le) == 'gc_timeout':
            return jsonify({'error': 'geocache inaccessible temporairement (délai dépassé)'}), 503
        return jsonify({'error': 'not_found', 'detail': str(le)}), 404
    except Exception as e:
        logger.error(f"Unexpected error during import: {type(e).__name__}: {e}", exc_info=True)
        return jsonify({'error': 'server_error', 'detail': str(e)}), 500


@bp.get('/api/zones/<int:zone_id>/geocaches')
def list_zone_geocaches(zone_id: int):
    logger.debug(f"GET /api/zones/{zone_id}/geocaches called")
    Zone.query.get_or_404(zone_id)
    items = Geocache.query.filter_by(zone_id=zone_id).order_by(Geocache.id.desc()).all()
    logger.debug(f"Found {len(items)} geocaches in zone {zone_id}")
    return jsonify([g.to_list_item() for g in items])


@bp.get('/api/geocaches/<int:geocache_id>')
def get_geocache(geocache_id: int):
    logger.debug(f"GET /api/geocaches/{geocache_id} called")
    g = Geocache.query.get_or_404(geocache_id)
    logger.debug(f"Retrieved geocache {g.gc_code} (id={g.id})")
    return jsonify(g.to_dict())


