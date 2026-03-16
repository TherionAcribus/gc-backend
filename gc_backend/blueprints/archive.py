"""
Blueprint pour l'API d'archive de résolution des géocaches.

Permet de consulter, rechercher, gérer et supprimer les archives de résolution.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..geocaches.archive_service import ArchiveService
from ..geocaches.models import Geocache, SolvedGeocacheArchive

bp = Blueprint('archive', __name__)
logger = logging.getLogger(__name__)


@bp.get('/api/archive')
def list_archives():
    """
    Liste toutes les archives de résolution.

    Query params:
        - page (int, default 1)
        - per_page (int, default 50, max 200)
        - solved_status (str): filtre sur le statut
        - gc_code (str): filtre sur le code GC (recherche partielle)
    """
    try:
        page = max(1, request.args.get('page', 1, type=int))
        per_page = min(200, max(1, request.args.get('per_page', 50, type=int)))
        solved_status = request.args.get('solved_status')
        gc_code_filter = (request.args.get('gc_code') or '').strip().upper()

        query = SolvedGeocacheArchive.query

        if solved_status:
            query = query.filter(SolvedGeocacheArchive.solved_status == solved_status)
        if gc_code_filter:
            query = query.filter(SolvedGeocacheArchive.gc_code.like(f'%{gc_code_filter}%'))

        query = query.order_by(SolvedGeocacheArchive.updated_at.desc())

        total = query.count()
        entries = query.offset((page - 1) * per_page).limit(per_page).all()

        return jsonify({
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page,
            'archives': [e.to_dict() for e in entries],
        })
    except Exception as e:
        logger.error(f"Error listing archives: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.get('/api/archive/<string:gc_code>')
def get_archive(gc_code: str):
    """Récupère l'archive d'un GC code spécifique."""
    try:
        archive = ArchiveService.get_by_gc_code(gc_code)
        if archive is None:
            return jsonify({'error': 'Archive not found'}), 404
        return jsonify(archive)
    except Exception as e:
        logger.error(f"Error fetching archive for {gc_code}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.get('/api/archive/<string:gc_code>/status')
def get_archive_status(gc_code: str):
    """
    Retourne le statut de synchronisation de l'archive pour un GC code.
    Utile pour le badge de synchro côté frontend.
    """
    try:
        code = gc_code.strip().upper()
        archive = ArchiveService.get_by_gc_code(code)

        if archive is None:
            # Vérifier si la geocache est dans un état qui mérite une archive
            geocache = Geocache.query.filter(Geocache.gc_code == code).first()
            if geocache is None:
                return jsonify({'exists': False, 'synced': False, 'gc_code': code})
            solved = geocache.solved or 'not_solved'
            is_corrected = geocache.is_corrected or False
            found = geocache.found or False
            if solved in ('in_progress', 'solved') or is_corrected or found:
                return jsonify({'exists': False, 'synced': False, 'gc_code': code, 'needs_sync': True})
            return jsonify({'exists': False, 'synced': True, 'gc_code': code, 'needs_sync': False})

        return jsonify({
            'exists': True,
            'synced': True,
            'gc_code': code,
            'solved_status': archive.get('solved_status'),
            'updated_at': archive.get('updated_at'),
        })
    except Exception as e:
        logger.error(f"Error fetching archive status for {gc_code}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.delete('/api/archive/<string:gc_code>')
def delete_archive(gc_code: str):
    """Supprime explicitement l'archive d'un GC code."""
    try:
        deleted = ArchiveService.delete_archive(gc_code)
        if not deleted:
            return jsonify({'error': 'Archive not found'}), 404
        return jsonify({'deleted': True, 'gc_code': gc_code.strip().upper()})
    except Exception as e:
        logger.error(f"Error deleting archive for {gc_code}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.get('/api/archive/stats')
def get_archive_stats():
    """Retourne les statistiques globales de résolution."""
    try:
        stats = ArchiveService.get_stats()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error fetching archive stats: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.post('/api/archive/<string:gc_code>/restore')
def restore_archive(gc_code: str):
    """
    Restaure manuellement les données d'archive vers une geocache existante.
    Utile si la restauration automatique n'a pas eu lieu ou a été ignorée.
    """
    try:
        code = gc_code.strip().upper()
        archive = ArchiveService.get_by_gc_code(code)
        if archive is None:
            return jsonify({'error': 'Archive not found'}), 404

        geocache = Geocache.query.filter(Geocache.gc_code == code).first()
        if geocache is None:
            return jsonify({'error': 'Geocache not found in database. Import it first.'}), 404

        from ..database import db

        restored_fields = []

        if archive.get('solved_status') and archive['solved_status'] != 'not_solved':
            geocache.solved = archive['solved_status']
            restored_fields.append('solved_status')

        if archive.get('solved_coordinates_raw'):
            geocache.coordinates_raw = archive['solved_coordinates_raw']
            geocache.latitude = archive.get('solved_latitude')
            geocache.longitude = archive.get('solved_longitude')
            geocache.is_corrected = True
            restored_fields.append('coordinates')

        if archive.get('personal_note'):
            geocache.gc_personal_note = archive['personal_note']
            restored_fields.append('personal_note')

        if archive.get('found') and not geocache.found:
            geocache.found = archive['found']
            restored_fields.append('found')

        db.session.commit()

        logger.info(f"Archive restored for {code}: {restored_fields}")
        return jsonify({
            'restored': True,
            'gc_code': code,
            'restored_fields': restored_fields,
            'geocache': geocache.to_dict(),
        })
    except Exception as e:
        from ..database import db
        db.session.rollback()
        logger.error(f"Error restoring archive for {gc_code}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.post('/api/archive/<string:gc_code>/sync')
def force_sync_archive(gc_code: str):
    """
    Force la synchronisation de l'archive depuis la géocache courante.
    Utile pour le bouton "sync" du badge dans le frontend.
    """
    try:
        code = gc_code.strip().upper()
        geocache = Geocache.query.filter(Geocache.gc_code == code).first()
        if geocache is None:
            return jsonify({'error': 'Geocache not found in database'}), 404

        from ..geocaches.archive_service import ArchiveService
        synced = ArchiveService.sync_from_geocache(geocache, force=True)
        archive = ArchiveService.get_by_gc_code(code)

        return jsonify({
            'synced': synced,
            'gc_code': code,
            'archive': archive,
        })
    except Exception as e:
        logger.error(f"Error force-syncing archive for {gc_code}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@bp.put('/api/archive/<string:gc_code>/formula-data')
def update_formula_data(gc_code: str):
    """
    Met à jour le snapshot du formula solver pour un GC code.

    Body JSON:
        {
            "formula": "N 47° AB.CDE E 006° FG.HIJ",
            "variables": {"A": 1, "B": 2, ...},
            "result": "N 47° 12.345 E 006° 78.901"
        }
    """
    try:
        data = request.get_json(silent=True) or {}
        if not data:
            return jsonify({'error': 'No data provided'}), 400

        code = gc_code.strip().upper()
        archive = ArchiveService.get_by_gc_code(code)
        if archive is None:
            return jsonify({'error': 'Archive not found for this gc_code'}), 404

        updated = ArchiveService.update_formula_data(code, data)
        if not updated:
            return jsonify({'error': 'Failed to update formula data'}), 500

        return jsonify({'updated': True, 'gc_code': code})
    except Exception as e:
        logger.error(f"Error updating formula data for {gc_code}: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
