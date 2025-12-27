"""Blueprint pour la gestion des logs des géocaches.

Ce module fournit les routes API pour :
- Récupérer les logs stockés d'une géocache
- Rafraîchir les logs depuis Geocaching.com
- Filtrer les logs par type
"""

import logging
from datetime import date as date_type
from datetime import datetime

from flask import Blueprint, jsonify, request

from ..database import db
from ..geocaches.models import Geocache, GeocacheLog
from ..services.geocaching_logs import GeocachingLogsClient
from ..services.geocaching_submit_logs import GeocachingSubmitLogsClient

bp = Blueprint('logs', __name__)
logger = logging.getLogger(__name__)


@bp.get('/api/geocaches/<int:geocache_id>/logs')
def get_geocache_logs(geocache_id: int):
    """
    Récupère les logs stockés d'une géocache.
    
    Query params:
        - limit: Nombre maximum de logs à retourner (défaut: 50)
        - offset: Offset pour la pagination (défaut: 0)
        - type: Filtrer par type de log (ex: Found, Note, Did Not Find)
    
    Returns:
        JSON avec la liste des logs et métadonnées de pagination
    """
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        # Paramètres de pagination
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        log_type_filter = request.args.get('type', None)
        
        # Construire la requête
        query = GeocacheLog.query.filter_by(geocache_id=geocache_id)
        
        # Filtrer par type si spécifié
        if log_type_filter:
            query = query.filter(GeocacheLog.log_type == log_type_filter)
        
        # Compter le total avant pagination
        total_count = query.count()
        
        # Appliquer tri et pagination
        logs = query.order_by(GeocacheLog.date.desc()) \
                    .offset(offset) \
                    .limit(limit) \
                    .all()
        
        logger.info(f"Returning {len(logs)} logs for geocache {geocache.gc_code} (total: {total_count})")
        
        return jsonify({
            'geocache_id': geocache_id,
            'gc_code': geocache.gc_code,
            'total_count': total_count,
            'offset': offset,
            'limit': limit,
            'logs': [log.to_dict() for log in logs]
        })
        
    except Exception as e:
        logger.error(f"Error fetching logs for geocache {geocache_id}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/<int:geocache_id>/logs/submit')
def submit_geocache_log(geocache_id: int):
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404

        gc_code = geocache.gc_code
        if not gc_code:
            return jsonify({'error': 'Geocache has no GC code'}), 400

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({'error': 'Invalid JSON payload'}), 400

        text = data.get('text')
        if not isinstance(text, str) or not text.strip():
            return jsonify({'error': 'Missing log text'}), 400

        raw_date = data.get('date')
        if not isinstance(raw_date, str) or not raw_date.strip():
            return jsonify({'error': 'Missing log date'}), 400
        try:
            visited_date: date_type = datetime.strptime(raw_date.strip(), '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid date format (expected YYYY-MM-DD)'}), 400

        log_type = data.get('logType')
        log_type_id = data.get('logTypeId')

        if isinstance(log_type_id, int):
            resolved_log_type_id = log_type_id
        elif isinstance(log_type, str):
            key = log_type.strip().lower()
            mapping = {
                'found': 2,
                'found_it': 2,
                'found it': 2,
                'dnf': 3,
                "didn't find it": 3,
                "didnt find it": 3,
                'note': 4,
                'write note': 4,
            }
            resolved_log_type_id = mapping.get(key)
        else:
            resolved_log_type_id = None

        if not isinstance(resolved_log_type_id, int):
            return jsonify({'error': 'Missing/invalid log type (use logType or logTypeId)'}), 400

        favorite = data.get('favorite')
        used_favorite_point = None
        if isinstance(favorite, bool):
            used_favorite_point = favorite

        client = GeocachingSubmitLogsClient()
        result = client.submit_geocache_log(
            gc_code,
            log_type_id=resolved_log_type_id,
            log_text=text,
            visited_date=visited_date,
            used_favorite_point=used_favorite_point,
        )
        if not result:
            return jsonify({'error': 'Failed to submit log to Geocaching.com'}), 502

        if not isinstance(result, dict) or not result.get('logReferenceCode'):
            return jsonify({'error': 'Geocaching.com did not return a logReferenceCode', 'gc_response': result}), 502

        return jsonify({
            'geocache_id': geocache_id,
            'gc_code': gc_code,
            'submitted': True,
            'gc_response': result,
            'log_reference_code': result.get('logReferenceCode') if isinstance(result, dict) else None,
        })

    except Exception as e:  # pragma: no cover
        logger.error('Error submitting log for geocache %s: %s', geocache_id, e)
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.post('/api/geocaches/<int:geocache_id>/logs/refresh')
def refresh_geocache_logs(geocache_id: int):
    """
    Rafraîchit les logs d'une géocache depuis Geocaching.com.
    
    Query params:
        - count: Nombre de logs à récupérer (défaut: 25)
    
    Returns:
        JSON avec le nombre de logs ajoutés/mis à jour
    """
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        gc_code = geocache.gc_code
        if not gc_code:
            return jsonify({'error': 'Geocache has no GC code'}), 400
        
        # Paramètres
        count = request.args.get('count', 25, type=int)
        
        logger.info(f"Refreshing logs for {gc_code} (count={count})")
        
        # Récupérer les logs depuis Geocaching.com
        client = GeocachingLogsClient()
        fetched_logs = client.get_logs(gc_code, count=count)
        
        if not fetched_logs:
            logger.warning(f"No logs found for {gc_code}")
            return jsonify({
                'geocache_id': geocache_id,
                'gc_code': gc_code,
                'message': 'No logs found on Geocaching.com',
                'added': 0,
                'updated': 0
            })
        
        # Récupérer les logs existants par external_id
        existing_logs = {
            log.external_id: log 
            for log in GeocacheLog.query.filter_by(geocache_id=geocache_id).all()
            if log.external_id
        }
        
        added_count = 0
        updated_count = 0
        
        for log_data in fetched_logs:
            if log_data.external_id in existing_logs:
                # Mettre à jour le log existant
                existing_log = existing_logs[log_data.external_id]
                existing_log.text = log_data.text
                existing_log.log_type = GeocacheLog.normalize_log_type(log_data.log_type)
                existing_log.is_favorite = log_data.is_favorite
                updated_count += 1
            else:
                # Créer un nouveau log
                new_log = GeocacheLog(
                    geocache_id=geocache_id,
                    external_id=log_data.external_id,
                    author=log_data.author,
                    author_guid=log_data.author_guid,
                    text=log_data.text,
                    date=log_data.date,
                    log_type=GeocacheLog.normalize_log_type(log_data.log_type),
                    is_favorite=log_data.is_favorite,
                )
                db.session.add(new_log)
                added_count += 1
        
        # Mettre à jour le compteur de logs
        geocache.logs_count = GeocacheLog.query.filter_by(geocache_id=geocache_id).count()
        
        db.session.commit()
        
        logger.info(f"Refreshed logs for {gc_code}: {added_count} added, {updated_count} updated")
        
        return jsonify({
            'geocache_id': geocache_id,
            'gc_code': gc_code,
            'message': 'Logs refreshed successfully',
            'added': added_count,
            'updated': updated_count,
            'total': geocache.logs_count
        })
        
    except LookupError as e:
        logger.warning(f"Geocache not found on Geocaching.com: {e}")
        return jsonify({'error': 'Geocache not found on Geocaching.com'}), 404
        
    except Exception as e:
        logger.error(f"Error refreshing logs for geocache {geocache_id}: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@bp.get('/api/geocaches/<int:geocache_id>/logs/types')
def get_log_types(geocache_id: int):
    """
    Récupère les types de logs disponibles pour une géocache avec leur compte.
    
    Returns:
        JSON avec la liste des types et leur nombre
    """
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        # Compter les logs par type
        from sqlalchemy import func
        type_counts = db.session.query(
            GeocacheLog.log_type,
            func.count(GeocacheLog.id)
        ).filter_by(geocache_id=geocache_id) \
         .group_by(GeocacheLog.log_type) \
         .all()
        
        types = [
            {'type': log_type, 'count': count}
            for log_type, count in type_counts
        ]
        
        return jsonify({
            'geocache_id': geocache_id,
            'types': types
        })
        
    except Exception as e:
        logger.error(f"Error fetching log types for geocache {geocache_id}: {e}")
        return jsonify({'error': str(e)}), 500


@bp.delete('/api/geocaches/<int:geocache_id>/logs')
def delete_geocache_logs(geocache_id: int):
    """
    Supprime tous les logs d'une géocache.
    
    Returns:
        JSON avec le nombre de logs supprimés
    """
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({'error': 'Geocache not found'}), 404
        
        # Compter et supprimer les logs
        deleted_count = GeocacheLog.query.filter_by(geocache_id=geocache_id).delete()
        
        # Mettre à jour le compteur
        geocache.logs_count = 0
        
        db.session.commit()
        
        logger.info(f"Deleted {deleted_count} logs for geocache {geocache.gc_code}")
        
        return jsonify({
            'geocache_id': geocache_id,
            'deleted': deleted_count
        })
        
    except Exception as e:
        logger.error(f"Error deleting logs for geocache {geocache_id}: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
