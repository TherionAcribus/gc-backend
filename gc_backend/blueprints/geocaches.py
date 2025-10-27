from flask import Blueprint, jsonify, request
import logging

from ..database import db
from ..geocaches.models import Geocache
from ..geocaches.importer import GeocacheImporter
from ..geocaches.scraper import GeocachingScraper

bp = Blueprint('geocaches', __name__)
logger = logging.getLogger(__name__)


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
                'owner': gc.owner,
                'cache_type': gc.type,  # Le frontend attend 'cache_type'
                'difficulty': gc.difficulty,
                'terrain': gc.terrain,
                'size': gc.size,
                'solved': 'not_solved',  # TODO: ajouter ce champ au modèle
                'found': gc.found or False,
                'favorites_count': gc.favorites_count or 0,
                'hidden_date': gc.placed_at.isoformat() if gc.placed_at else None,
            })
        
        logger.info(f"Returning {len(result)} geocaches for zone {zone_id}")
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error fetching geocaches for zone {zone_id}: {e}")
        return jsonify({'error': str(e)}), 500


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
        
        db.session.delete(geocache)
        db.session.commit()
        
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
        zone_id = geocache.zone_id
        
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
        geocache.description_html = getattr(s, 'description_html', None)
        geocache.hints = getattr(s, 'hints', None)
        geocache.attributes = getattr(s, 'attributes', None)
        geocache.favorites_count = getattr(s, 'favorites_count', None)
        geocache.logs_count = getattr(s, 'logs_count', None)
        geocache.images = getattr(s, 'images', None)
        
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
