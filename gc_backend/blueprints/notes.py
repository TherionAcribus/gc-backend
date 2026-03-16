from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from ..database import db
from ..geocaches.models import Geocache, Note, GeocacheNote
from ..geocaches.archive_service import ArchiveService
from ..services.geocaching_personal_notes import GeocachingPersonalNotesClient

bp = Blueprint("notes", __name__)
logger = logging.getLogger(__name__)


@bp.get("/api/geocaches/<int:geocache_id>/notes")
def get_geocache_notes(geocache_id: int):
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({"error": "Geocache not found"}), 404

        query = Note.query.join(GeocacheNote).filter(GeocacheNote.geocache_id == geocache_id)
        notes = query.order_by(Note.created_at.desc()).all()

        return jsonify(
            {
                "geocache_id": geocache.id,
                "gc_code": geocache.gc_code,
                "name": geocache.name,
                "gc_personal_note": geocache.gc_personal_note,
                "gc_personal_note_synced_at": geocache.gc_personal_note_synced_at.isoformat()
                if geocache.gc_personal_note_synced_at
                else None,
                "gc_personal_note_last_pushed_at": geocache.gc_personal_note_last_pushed_at.isoformat()
                if geocache.gc_personal_note_last_pushed_at
                else None,
                "notes": [note.to_dict() for note in notes],
            }
        )
    except Exception as e:  # pragma: no cover
        logger.error("Error fetching notes for geocache %s: %s", geocache_id, e)
        return jsonify({"error": str(e)}), 500


@bp.post("/api/geocaches/<int:geocache_id>/notes")
def create_geocache_note(geocache_id: int):
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({"error": "Geocache not found"}), 404

        data = request.get_json(silent=True) or {}
        content = (data.get("content") or "").strip()
        if not content:
            return jsonify({"error": "content is required"}), 400

        note_type = (data.get("note_type") or "user").strip() or "user"
        source = (data.get("source") or "user").strip() or "user"
        source_plugin = data.get("source_plugin")

        note = Note(content=content, note_type=note_type, source=source, source_plugin=source_plugin)
        db.session.add(note)
        db.session.flush()

        link = GeocacheNote(geocache_id=geocache.id, note_id=note.id)
        db.session.add(link)
        db.session.commit()

        ArchiveService.sync_from_geocache(geocache)

        return jsonify({"note": note.to_dict(), "geocache_id": geocache.id}), 201
    except Exception as e:  # pragma: no cover
        logger.error("Error creating note for geocache %s: %s", geocache_id, e)
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.put("/api/notes/<int:note_id>")
def update_note(note_id: int):
    try:
        note = Note.query.get(note_id)
        if not note:
            return jsonify({"error": "Note not found"}), 404

        if note.source != "user":
            return jsonify({"error": "Only user notes can be edited"}), 400

        data = request.get_json(silent=True) or {}

        if "content" in data:
            content = (data.get("content") or "").strip()
            if not content:
                return jsonify({"error": "content cannot be empty"}), 400
            note.content = content

        if "note_type" in data:
            note_type = (data.get("note_type") or "").strip()
            if note_type:
                note.note_type = note_type

        db.session.commit()

        # Sync archive for all geocaches linked to this note
        for link in GeocacheNote.query.filter_by(note_id=note_id).all():
            gc = Geocache.query.get(link.geocache_id)
            if gc:
                ArchiveService.sync_from_geocache(gc)

        return jsonify({"note": note.to_dict()})
    except Exception as e:  # pragma: no cover
        logger.error("Error updating note %s: %s", note_id, e)
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.delete("/api/notes/<int:note_id>")
def delete_note(note_id: int):
    try:
        note = Note.query.get(note_id)
        if not note:
            return jsonify({"error": "Note not found"}), 404

        GeocacheNote.query.filter_by(note_id=note_id).delete()
        db.session.delete(note)
        db.session.commit()

        return jsonify({"deleted": True})
    except Exception as e:  # pragma: no cover
        logger.error("Error deleting note %s: %s", note_id, e)
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.post("/api/geocaches/<int:geocache_id>/notes/sync-from-geocaching")
def sync_notes_from_geocaching(geocache_id: int):
    try:
        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({"error": "Geocache not found"}), 404

        if not geocache.gc_code:
            return jsonify({"error": "Geocache has no GC code"}), 400

        client = GeocachingPersonalNotesClient()
        note_text = client.get_personal_note(geocache.gc_code)

        geocache.gc_personal_note = note_text
        geocache.gc_personal_note_synced_at = datetime.now(timezone.utc)
        db.session.commit()

        return jsonify(
            {
                "geocache_id": geocache.id,
                "gc_code": geocache.gc_code,
                "gc_personal_note": geocache.gc_personal_note,
                "gc_personal_note_synced_at": geocache.gc_personal_note_synced_at.isoformat()
                if geocache.gc_personal_note_synced_at
                else None,
            }
        )
    except Exception as e:  # pragma: no cover
        logger.error("Error syncing personal note from Geocaching.com for geocache %s: %s", geocache_id, e)
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@bp.post("/api/notes/<int:note_id>/sync-to-geocaching")
def sync_note_to_geocaching(note_id: int):
    try:
        note = Note.query.get(note_id)
        if not note:
            return jsonify({"error": "Note not found"}), 404

        if note.source != "user":
            return jsonify({"error": "Only user notes can be synced to Geocaching.com"}), 400

        geocache_id = request.args.get("geocacheId", type=int)
        if geocache_id is None:
            link = GeocacheNote.query.filter_by(note_id=note_id).first()
            if not link:
                return jsonify({"error": "No geocache associated with this note"}), 400
            geocache_id = link.geocache_id

        geocache = Geocache.query.get(geocache_id)
        if not geocache:
            return jsonify({"error": "Geocache not found"}), 404

        if not geocache.gc_code:
            return jsonify({"error": "Geocache has no GC code"}), 400

        # Permettre au frontend de fournir un contenu final (remplacer/ajouter) pour la note GC.com
        data = request.get_json(silent=True) or {}
        override_content = data.get("content") if isinstance(data, dict) else None
        if isinstance(override_content, str) and override_content.strip():
            payload_content = override_content
        else:
            payload_content = note.content or ""

        client = GeocachingPersonalNotesClient()
        ok = client.update_personal_note(geocache.gc_code, payload_content)
        if not ok:
            return jsonify({"error": "Failed to update personal note on Geocaching.com"}), 502

        geocache.gc_personal_note = payload_content
        geocache.gc_personal_note_last_pushed_at = datetime.now(timezone.utc)
        db.session.commit()

        return jsonify(
            {
                "geocache_id": geocache.id,
                "gc_code": geocache.gc_code,
                "gc_personal_note": geocache.gc_personal_note,
                "gc_personal_note_last_pushed_at": geocache.gc_personal_note_last_pushed_at.isoformat()
                if geocache.gc_personal_note_last_pushed_at
                else None,
            }
        )
    except Exception as e:  # pragma: no cover
        logger.error("Error syncing note %s to Geocaching.com: %s", note_id, e)
        db.session.rollback()
        return jsonify({"error": str(e)}), 500
