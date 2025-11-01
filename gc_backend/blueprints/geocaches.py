from flask import Blueprint, jsonify, request, Response, stream_with_context
import logging
import io
import zipfile
import xml.etree.ElementTree as ET
import json

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
                'solved': gc.solved or 'not_solved',
                'found': gc.found or False,
                'favorites_count': gc.favorites_count or 0,
                'hidden_date': gc.placed_at.isoformat() if gc.placed_at else None,
                'latitude': gc.latitude,
                'longitude': gc.longitude,
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
            note=data.get('note')
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
        waypoint.note = data.get('note', waypoint.note)
        
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
        return jsonify(waypoint.to_dict())
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Erreur lors de la mise à jour du waypoint: {str(e)}")
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
        
        logger.info(f"[SET CORRECTED COORDS] Geocache {geocache_id} - Waypoint {waypoint_id}")
        logger.info(f"[SET CORRECTED COORDS] Anciennes coords: lat={geocache.latitude}, lon={geocache.longitude}")
        logger.info(f"[SET CORRECTED COORDS] Nouvelles coords: lat={waypoint.latitude}, lon={waypoint.longitude}")
        
        # Sauvegarder les coordonnées originales si ce n'est pas déjà fait
        if not geocache.is_corrected:
            geocache.original_latitude = geocache.latitude
            geocache.original_longitude = geocache.longitude
        
        # Mettre à jour avec les coordonnées du waypoint
        geocache.latitude = waypoint.latitude
        geocache.longitude = waypoint.longitude
        geocache.is_corrected = True
        
        db.session.commit()
        
        logger.info(f"[SET CORRECTED COORDS] Coordonnées corrigées mises à jour")
        
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
        from ..geocaches.scraper import GeocachingScraper
        
        def parse_gc_coordinates(coords_text: str):
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
        
        lat, lon = parse_gc_coordinates(coordinates_raw)
        if lat is None or lon is None:
            return jsonify({'error': 'Invalid coordinates format'}), 400
        
        # Mettre à jour les coordonnées
        geocache.coordinates_raw = coordinates_raw
        geocache.latitude = lat
        geocache.longitude = lon
        geocache.is_corrected = True
        
        db.session.commit()
        
        logger.info(f"Updated coordinates for geocache {geocache_id}")
        return jsonify({'success': True, 'geocache': geocache.to_dict()})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating coordinates: {e}", exc_info=True)
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
        
        logger.info(f"Reset coordinates for geocache {geocache_id}")
        return jsonify({'success': True, 'geocache': geocache.to_dict()})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error resetting coordinates: {e}", exc_info=True)
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
        
        logger.info(f"Updated solved status for geocache {geocache_id} to {solved_status}")
        return jsonify({'success': True, 'solved': solved_status})
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating solved status: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500