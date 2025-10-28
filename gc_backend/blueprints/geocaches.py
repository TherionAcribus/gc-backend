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