"""Blueprint REST dédié à la gestion des images de géocaches (métadonnées + stockage local optionnel)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, jsonify, request, send_file

from ..database import db
from ..geocaches.image_storage import (
    download_image,
    get_images_root_dir,
    remove_geocache_dir,
    write_image_file,
)
from ..geocaches.image_sync import ensure_images_v2_for_geocache
from ..geocaches.models import Geocache, GeocacheImage


bp = Blueprint('geocache_images', __name__)
logger = logging.getLogger(__name__)


def _safe_resolve_stored_file(stored_path: str) -> Path:
    root = get_images_root_dir().resolve()
    full_path = (root / stored_path).resolve()
    if root not in full_path.parents and root != full_path:
        raise ValueError('Invalid stored path')
    return full_path


@bp.get('/api/geocaches/<int:geocache_id>/images')
def list_geocache_images(geocache_id: int):
    geocache = Geocache.query.get(geocache_id)
    if not geocache:
        return jsonify({'error': 'Geocache not found'}), 404

    ensure_images_v2_for_geocache(geocache)
    db.session.commit()

    images = GeocacheImage.query.filter_by(geocache_id=geocache_id).order_by(GeocacheImage.id.asc()).all()
    return jsonify([img.to_dict() for img in images])


@bp.patch('/api/geocache-images/<int:image_id>')
def patch_geocache_image(image_id: int):
    image = GeocacheImage.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    payload = request.get_json(silent=True) or {}

    allowed: Dict[str, Any] = {
        'title': str,
        'note': str,
        'tags': list,
        'detected_features': dict,
        'qr_payload': str,
        'ocr_text': str,
        'ocr_language': str,
    }

    for key, expected in allowed.items():
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None:
            setattr(image, key, None)
            continue
        if expected is list and not isinstance(value, list):
            return jsonify({'error': f'Invalid type for {key}'}), 400
        if expected is dict and not isinstance(value, dict):
            return jsonify({'error': f'Invalid type for {key}'}), 400
        if expected is str and not isinstance(value, str):
            return jsonify({'error': f'Invalid type for {key}'}), 400
        setattr(image, key, value)

    db.session.commit()
    return jsonify(image.to_dict())


@bp.post('/api/geocache-images/<int:image_id>/store')
def store_geocache_image(image_id: int):
    image = GeocacheImage.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    if image.stored and image.stored_path:
        return jsonify(image.to_dict())

    try:
        content, content_type, status_code = download_image(image.source_url)
        if status_code >= 400:
            return jsonify({'error': f'Failed to download image (HTTP {status_code})'}), 502

        stored_path, mime_type, byte_size, sha256 = write_image_file(
            geocache_id=image.geocache_id,
            image_id=image.id,
            content=content,
            content_type=content_type,
            source_url=image.source_url,
        )

        image.stored = True
        image.stored_path = stored_path
        image.mime_type = mime_type
        image.byte_size = byte_size
        image.sha256 = sha256

        db.session.commit()
        return jsonify(image.to_dict())
    except Exception as exc:
        logger.error('Failed to store image %s: %s', image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to store image'}), 500


@bp.post('/api/geocaches/<int:geocache_id>/images/store')
def store_all_geocache_images(geocache_id: int):
    geocache = Geocache.query.get(geocache_id)
    if not geocache:
        return jsonify({'error': 'Geocache not found'}), 404

    ensure_images_v2_for_geocache(geocache)
    db.session.commit()

    images = GeocacheImage.query.filter_by(geocache_id=geocache_id).order_by(GeocacheImage.id.asc()).all()

    stored_count = 0
    failed: list[dict] = []

    for img in images:
        if img.stored and img.stored_path:
            continue
        try:
            content, content_type, status_code = download_image(img.source_url)
            if status_code >= 400:
                failed.append({'id': img.id, 'status': status_code})
                continue

            stored_path, mime_type, byte_size, sha256 = write_image_file(
                geocache_id=img.geocache_id,
                image_id=img.id,
                content=content,
                content_type=content_type,
                source_url=img.source_url,
            )
            img.stored = True
            img.stored_path = stored_path
            img.mime_type = mime_type
            img.byte_size = byte_size
            img.sha256 = sha256
            stored_count += 1
        except Exception as exc:
            logger.warning('Failed storing image %s: %s', img.id, exc)
            failed.append({'id': img.id, 'error': str(exc)})

    db.session.commit()
    return jsonify({'stored': stored_count, 'failed': failed})


@bp.get('/api/geocache-images/<int:image_id>/content')
def get_geocache_image_content(image_id: int):
    image = GeocacheImage.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    if not image.stored or not image.stored_path:
        return jsonify({'error': 'Image not stored'}), 404

    try:
        full_path = _safe_resolve_stored_file(image.stored_path)
        if not full_path.exists():
            return jsonify({'error': 'Stored file missing'}), 404
        return send_file(full_path, mimetype=image.mime_type or None)
    except ValueError:
        return jsonify({'error': 'Invalid stored path'}), 400


@bp.post('/api/geocaches/<int:geocache_id>/images/cleanup')
def cleanup_geocache_images(geocache_id: int):
    """Endpoint utilitaire (optionnel) pour supprimer les fichiers stockés d'une géocache."""
    try:
        remove_geocache_dir(geocache_id)
        return jsonify({'message': 'ok'}), 200
    except Exception as exc:
        logger.error('Cleanup failed for geocache %s: %s', geocache_id, exc, exc_info=True)
        return jsonify({'error': 'cleanup_failed'}), 500
