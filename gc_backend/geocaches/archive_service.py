"""
Service d'archivage des données de résolution des géocaches.

Ce service gère la synchronisation automatique entre une Geocache et son archive
de résolution (SolvedGeocacheArchive). L'archive survit à la suppression de la
géocache et permet la restauration lors d'un rechargement.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Geocache

logger = logging.getLogger(__name__)

# Statuts qui déclenchent une synchronisation (on n'archive pas les caches non travaillées)
_ARCHIVE_STATUSES = {'in_progress', 'solved'}
_MAX_RESOLUTION_HISTORY_ENTRIES = 12


def _should_archive(geocache: Geocache) -> bool:
    """Retourne True si la géocache mérite d'être archivée."""
    solved = getattr(geocache, 'solved', None) or 'not_solved'
    is_corrected = getattr(geocache, 'is_corrected', False) or False
    found = getattr(geocache, 'found', False) or False
    has_notes = bool(getattr(geocache, 'notes', None))
    return solved in _ARCHIVE_STATUSES or is_corrected or found or has_notes


def _detect_resolution_method(geocache: Geocache) -> str | None:
    """Détecte la méthode de résolution à partir des données de la géocache."""
    is_corrected = getattr(geocache, 'is_corrected', False) or False
    solved = getattr(geocache, 'solved', None) or 'not_solved'
    if solved == 'not_solved' and not is_corrected:
        return None
    if is_corrected:
        return 'manual'
    return 'manual'


def _snapshot_notes(geocache: Geocache) -> str | None:
    """Crée un snapshot JSON des notes liées à la géocache."""
    notes = getattr(geocache, 'notes', None)
    if not notes:
        return None
    snapshot = []
    for note in notes:
        snapshot.append({
            'content': getattr(note, 'content', ''),
            'note_type': getattr(note, 'note_type', ''),
            'source': getattr(note, 'source', ''),
            'source_plugin': getattr(note, 'source_plugin', None),
            'created_at': note.created_at.isoformat() if getattr(note, 'created_at', None) else None,
            'updated_at': note.updated_at.isoformat() if getattr(note, 'updated_at', None) else None,
        })
    return json.dumps(snapshot, ensure_ascii=False) if snapshot else None


def _snapshot_waypoints(geocache: Geocache) -> str | None:
    """Crée un snapshot JSON des waypoints de la géocache."""
    waypoints = getattr(geocache, 'waypoints', None)
    if not waypoints:
        return None
    snapshot = []
    for wp in waypoints:
        snapshot.append({
            'prefix': getattr(wp, 'prefix', None),
            'lookup': getattr(wp, 'lookup', None),
            'name': getattr(wp, 'name', None),
            'type': getattr(wp, 'type', None),
            'latitude': getattr(wp, 'latitude', None),
            'longitude': getattr(wp, 'longitude', None),
            'gc_coords': getattr(wp, 'gc_coords', None),
            'note': getattr(wp, 'note', None),
            'note_override': getattr(wp, 'note_override', None),
        })
    return json.dumps(snapshot, ensure_ascii=False) if snapshot else None


def _build_resolution_history_signature(resume_state: dict) -> str | None:
    if not isinstance(resume_state, dict):
        return None

    signature_payload = {
        'currentText': resume_state.get('currentText'),
        'recommendationSourceText': resume_state.get('recommendationSourceText'),
        'classification': resume_state.get('classification'),
        'recommendation': resume_state.get('recommendation'),
        'workflowResolution': resume_state.get('workflowResolution'),
    }
    try:
        normalized = json.dumps(signature_payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    except TypeError:
        return None
    return hashlib.sha1(normalized.encode('utf-8')).hexdigest()


def _build_resolution_history_entry(resolution_diagnostics: dict) -> dict | None:
    if not isinstance(resolution_diagnostics, dict):
        return None

    resume_state = resolution_diagnostics.get('resume_state')
    if not isinstance(resume_state, dict):
        return None

    workflow_resolution = resume_state.get('workflowResolution') or {}
    workflow = workflow_resolution.get('workflow') or {}
    control = workflow_resolution.get('control') or {}
    workflow_entries = resume_state.get('workflowEntries') or []
    latest_entry = workflow_entries[0] if workflow_entries and isinstance(workflow_entries[0], dict) else None
    state_signature = _build_resolution_history_signature(resume_state)
    recorded_at = (
        resolution_diagnostics.get('updated_at')
        or resume_state.get('updatedAt')
        or datetime.now(timezone.utc).isoformat()
    )

    return {
        'entry_id': f"history-{(state_signature or recorded_at)[0:12]}",
        'recorded_at': recorded_at,
        'source': resolution_diagnostics.get('source') or 'plugin_executor_metasolver',
        'workflow_kind': workflow.get('kind'),
        'workflow_confidence': workflow.get('confidence'),
        'control_status': control.get('status'),
        'final_confidence': control.get('final_confidence'),
        'current_text': resume_state.get('currentText') or resolution_diagnostics.get('current_text'),
        'recommendation_source_text': resume_state.get('recommendationSourceText'),
        'latest_event': {
            'category': latest_entry.get('category'),
            'message': latest_entry.get('message'),
            'detail': latest_entry.get('detail'),
            'timestamp': latest_entry.get('timestamp'),
        } if latest_entry else None,
        'state_signature': state_signature,
        'resume_state': resume_state,
    }


def _extract_existing_resolution_history(existing_resolution_diagnostics: dict | None) -> list[dict]:
    if not isinstance(existing_resolution_diagnostics, dict):
        return []

    history = [
        item for item in (existing_resolution_diagnostics.get('history_state') or [])
        if isinstance(item, dict)
    ]
    if history:
        return history

    derived_current_entry = _build_resolution_history_entry(existing_resolution_diagnostics)
    return [derived_current_entry] if derived_current_entry else []


def _merge_resolution_diagnostics(existing_resolution_diagnostics: dict | None, incoming_resolution_diagnostics: dict) -> dict:
    merged = dict(incoming_resolution_diagnostics)
    existing_history = _extract_existing_resolution_history(existing_resolution_diagnostics)
    incoming_entry = _build_resolution_history_entry(incoming_resolution_diagnostics)

    merged_history: list[dict] = []
    seen_signatures: set[str] = set()

    def push_history_entry(entry: dict | None) -> None:
        if not isinstance(entry, dict):
            return
        signature = str(entry.get('state_signature') or '').strip()
        fallback_key = f"{entry.get('recorded_at')}::{entry.get('workflow_kind')}::{entry.get('current_text')}"
        dedupe_key = signature or fallback_key
        if dedupe_key in seen_signatures:
            return
        seen_signatures.add(dedupe_key)
        merged_history.append(entry)

    push_history_entry(incoming_entry)
    for item in existing_history:
        push_history_entry(item)

    merged['history_state'] = merged_history[:_MAX_RESOLUTION_HISTORY_ENTRIES]
    return merged


class ArchiveService:
    """Service statique pour la gestion de l'archive de résolution."""

    @staticmethod
    def sync_from_geocache(geocache: Geocache, force: bool = False) -> bool:
        """
        Synchronise l'archive depuis une Geocache.

        Ne synchronise que si la géocache est en état 'in_progress', 'solved',
        ou a des coordonnées corrigées / est trouvée, sauf si force=True.

        Respecte la préférence `geoApp.archive.autoSync.enabled` (défaut: True).
        Si désactivée, la synchronisation est ignorée sauf si force=True (snapshot avant suppression).

        Returns True si une entrée a été créée ou mise à jour, False sinon.
        """
        from ..database import db
        from .models import SolvedGeocacheArchive

        # Vérifier la préférence d'archivage automatique (ne bloque pas le force=True)
        if not force:
            try:
                from ..utils.preferences import get_value_or_default
                auto_sync_enabled = get_value_or_default('geoApp.archive.autoSync.enabled')
                if auto_sync_enabled is False:
                    logger.debug(f"Archive auto-sync disabled by preference, skipping sync for {geocache.gc_code}")
                    return False
            except Exception:
                pass  # En cas d'erreur, on continue normalement

        if not force and not _should_archive(geocache):
            return False

        gc_code = geocache.gc_code
        try:
            entry = SolvedGeocacheArchive.query.filter_by(gc_code=gc_code).first()
            now = datetime.now(timezone.utc)

            solved_status = getattr(geocache, 'solved', None) or 'not_solved'
            is_corrected = getattr(geocache, 'is_corrected', False) or False

            # Coordonnées résolues : si corrigées, les coordonnées affichées sont les coordonnées trouvées
            if is_corrected:
                solved_lat = getattr(geocache, 'latitude', None)
                solved_lon = getattr(geocache, 'longitude', None)
                solved_raw = getattr(geocache, 'coordinates_raw', None)
            else:
                solved_lat = None
                solved_lon = None
                solved_raw = None

            resolution_method = _detect_resolution_method(geocache)

            notes_snap = _snapshot_notes(geocache)
            wpts_snap = _snapshot_waypoints(geocache)

            if entry is None:
                entry = SolvedGeocacheArchive(
                    gc_code=gc_code,
                    name=geocache.name,
                    cache_type=geocache.type,
                    difficulty=geocache.difficulty,
                    terrain=geocache.terrain,
                    solved_status=solved_status,
                    solved_coordinates_raw=solved_raw,
                    solved_latitude=solved_lat,
                    solved_longitude=solved_lon,
                    original_coordinates_raw=getattr(geocache, 'original_coordinates_raw', None),
                    notes_snapshot=notes_snap,
                    personal_note=getattr(geocache, 'gc_personal_note', None),
                    waypoints_snapshot=wpts_snap,
                    found=getattr(geocache, 'found', None),
                    found_date=getattr(geocache, 'found_date', None),
                    resolution_method=resolution_method,
                    created_at=now,
                    updated_at=now,
                )
                db.session.add(entry)
            else:
                entry.name = geocache.name
                entry.cache_type = geocache.type
                entry.difficulty = geocache.difficulty
                entry.terrain = geocache.terrain
                entry.solved_status = solved_status
                entry.original_coordinates_raw = getattr(geocache, 'original_coordinates_raw', None)
                entry.personal_note = getattr(geocache, 'gc_personal_note', None)
                entry.found = getattr(geocache, 'found', None)
                entry.found_date = getattr(geocache, 'found_date', None)
                entry.updated_at = now

                # Mettre à jour les coordonnées résolues seulement si elles existent
                if solved_raw:
                    entry.solved_coordinates_raw = solved_raw
                    entry.solved_latitude = solved_lat
                    entry.solved_longitude = solved_lon

                # Mettre à jour méthode de résolution seulement si non null
                if resolution_method:
                    entry.resolution_method = resolution_method

                # Toujours rafraîchir les snapshots (ils reflètent l'état courant)
                if notes_snap is not None:
                    entry.notes_snapshot = notes_snap
                if wpts_snap is not None:
                    entry.waypoints_snapshot = wpts_snap

            db.session.commit()
            logger.debug(f"Archive synced for {gc_code} (status={solved_status})")
            return True

        except Exception as e:
            try:
                db.session.rollback()
            except Exception:
                pass
            logger.error(f"ArchiveService.sync_from_geocache error for {gc_code}: {e}", exc_info=True)
            return False

    @staticmethod
    def snapshot_before_delete(geocache: Geocache) -> bool:
        """
        Prend un snapshot complet avant suppression.
        Toujours exécuté si la géocache a du contenu pertinent.
        """
        solved = getattr(geocache, 'solved', None) or 'not_solved'
        is_corrected = getattr(geocache, 'is_corrected', False) or False
        found = getattr(geocache, 'found', False) or False
        has_notes = bool(getattr(geocache, 'notes', None))

        if solved == 'not_solved' and not is_corrected and not found and not has_notes:
            return False

        return ArchiveService.sync_from_geocache(geocache, force=True)

    @staticmethod
    def get_by_gc_code(gc_code: str) -> dict | None:
        """Retourne l'archive pour un code GC, ou None si inexistante."""
        from .models import SolvedGeocacheArchive
        entry = SolvedGeocacheArchive.query.filter_by(gc_code=gc_code.strip().upper()).first()
        return entry.to_dict() if entry else None

    @staticmethod
    def delete_archive(gc_code: str) -> bool:
        """Supprime explicitement l'archive d'un GC code."""
        from ..database import db
        from .models import SolvedGeocacheArchive
        entry = SolvedGeocacheArchive.query.filter_by(gc_code=gc_code.strip().upper()).first()
        if not entry:
            return False
        db.session.delete(entry)
        db.session.commit()
        logger.info(f"Archive deleted for {gc_code}")
        return True

    @staticmethod
    def update_formula_data(gc_code: str, formula_data: dict) -> bool:
        """Met à jour le snapshot formula solver pour un GC code."""
        from ..database import db
        from .models import SolvedGeocacheArchive
        code = gc_code.strip().upper()
        entry = SolvedGeocacheArchive.query.filter_by(gc_code=code).first()
        if not entry:
            return False
        try:
            entry.formula_data = json.dumps(formula_data, ensure_ascii=False)
            entry.updated_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"ArchiveService.update_formula_data error for {code}: {e}")
            return False

    @staticmethod
    def update_resolution_diagnostics(gc_code: str, resolution_diagnostics: dict) -> bool:
        """Met a jour le snapshot de diagnostic de resolution pour un GC code."""
        from ..database import db
        from .models import SolvedGeocacheArchive
        code = gc_code.strip().upper()
        entry = SolvedGeocacheArchive.query.filter_by(gc_code=code).first()
        if not entry:
            return False
        try:
            existing_resolution_diagnostics = None
            if entry.resolution_diagnostics:
                try:
                    existing_resolution_diagnostics = json.loads(entry.resolution_diagnostics)
                except Exception:
                    existing_resolution_diagnostics = None

            merged_resolution_diagnostics = _merge_resolution_diagnostics(
                existing_resolution_diagnostics,
                resolution_diagnostics,
            )
            entry.resolution_diagnostics = json.dumps(merged_resolution_diagnostics, ensure_ascii=False)
            entry.updated_at = datetime.now(timezone.utc)
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"ArchiveService.update_resolution_diagnostics error for {code}: {e}")
            return False

    @staticmethod
    def add_resolution_plugin(gc_code: str, plugin_name: str) -> bool:
        """Ajoute un plugin à la liste des plugins ayant contribué à la résolution."""
        from ..database import db
        from .models import SolvedGeocacheArchive
        code = gc_code.strip().upper()
        entry = SolvedGeocacheArchive.query.filter_by(gc_code=code).first()
        if not entry:
            return False
        try:
            current = []
            if entry.resolution_plugins:
                try:
                    current = json.loads(entry.resolution_plugins)
                except Exception:
                    current = []
            if plugin_name not in current:
                current.append(plugin_name)
                entry.resolution_plugins = json.dumps(current, ensure_ascii=False)
                entry.updated_at = datetime.now(timezone.utc)
                db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"ArchiveService.add_resolution_plugin error for {code}: {e}")
            return False

    @staticmethod
    def get_stats() -> dict:
        """Retourne les statistiques globales de résolution."""
        from .models import SolvedGeocacheArchive
        from sqlalchemy import func
        from ..database import db

        total = SolvedGeocacheArchive.query.count()
        solved_count = SolvedGeocacheArchive.query.filter_by(solved_status='solved').count()
        in_progress_count = SolvedGeocacheArchive.query.filter_by(solved_status='in_progress').count()
        found_count = SolvedGeocacheArchive.query.filter(SolvedGeocacheArchive.found.is_(True)).count()

        # Par type de cache
        by_type_rows = db.session.query(
            SolvedGeocacheArchive.cache_type,
            func.count(SolvedGeocacheArchive.id)
        ).filter(
            SolvedGeocacheArchive.solved_status == 'solved'
        ).group_by(SolvedGeocacheArchive.cache_type).all()
        by_type = {row[0] or 'Unknown': row[1] for row in by_type_rows}

        # Par méthode de résolution
        by_method_rows = db.session.query(
            SolvedGeocacheArchive.resolution_method,
            func.count(SolvedGeocacheArchive.id)
        ).filter(
            SolvedGeocacheArchive.resolution_method.isnot(None)
        ).group_by(SolvedGeocacheArchive.resolution_method).all()
        by_method = {row[0]: row[1] for row in by_method_rows}

        return {
            'total_archived': total,
            'solved': solved_count,
            'in_progress': in_progress_count,
            'found': found_count,
            'by_cache_type': by_type,
            'by_resolution_method': by_method,
        }
