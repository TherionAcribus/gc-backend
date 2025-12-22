"""Blueprint REST dédié à la gestion des images de géocaches (métadonnées + stockage local optionnel)."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict

from flask import Blueprint, jsonify, request, send_file
from werkzeug.utils import secure_filename

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

_MAX_RENDERED_FILE_BYTES = 10 * 1024 * 1024
_MAX_EDITOR_STATE_BYTES = 1024 * 1024
_ALLOWED_RENDERED_MIME_TYPES = {
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/webp',
}


def _get_root_image(image: GeocacheImage) -> GeocacheImage:
    current = image
    visited = set()
    while current.parent_image_id:
        if current.id in visited:
            break
        visited.add(current.id)
        parent = GeocacheImage.query.get(current.parent_image_id)
        if not parent:
            break
        current = parent
    return current


def _next_derivation_type(source: GeocacheImage, parent_image_id: int, base: str) -> str:
    existing_types = (
        db.session.query(GeocacheImage.derivation_type)
        .filter_by(
            geocache_id=source.geocache_id,
            source_url=source.source_url,
            parent_image_id=parent_image_id,
        )
        .all()
    )
    used = {row[0] for row in existing_types if row and row[0]}
    if base not in used:
        return base

    max_suffix = 1
    for derivation_type in used:
        if not derivation_type.startswith(base):
            continue
        suffix = derivation_type[len(base):]
        if not suffix:
            max_suffix = max(max_suffix, 1)
            continue
        if suffix.isdigit():
            max_suffix = max(max_suffix, int(suffix))

    candidate = f"{base}{max_suffix + 1}"
    if len(candidate) > 20:
        return base
    return candidate


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


@bp.get('/api/geocache-images/<int:image_id>/editor-state')
def get_geocache_image_editor_state(image_id: int):
    image = GeocacheImage.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    return jsonify({'editor_state_json': image.editor_state_json})


def _get_uploaded_rendered_file():
    uploaded = request.files.get('rendered_file')
    if not uploaded:
        return None, jsonify({'error': 'rendered_file is required'}), 400

    content = uploaded.read(_MAX_RENDERED_FILE_BYTES + 1)
    if not content:
        return None, jsonify({'error': 'rendered_file is empty'}), 400
    if len(content) > _MAX_RENDERED_FILE_BYTES:
        return None, jsonify({'error': 'rendered_file is too large'}), 413

    content_type = (uploaded.mimetype or request.form.get('mime_type') or '').split(';')[0].strip().lower()
    if content_type not in _ALLOWED_RENDERED_MIME_TYPES:
        return None, jsonify({'error': 'Unsupported mime type'}), 400

    is_png = content.startswith(b'\x89PNG\r\n\x1a\n')
    is_jpeg = content.startswith(b'\xff\xd8')
    is_webp = content.startswith(b'RIFF') and len(content) > 12 and content[8:12] == b'WEBP'
    if content_type == 'image/png' and not is_png:
        return None, jsonify({'error': 'Invalid PNG file'}), 400
    if content_type in {'image/jpeg', 'image/jpg'} and not is_jpeg:
        return None, jsonify({'error': 'Invalid JPEG file'}), 400
    if content_type == 'image/webp' and not is_webp:
        return None, jsonify({'error': 'Invalid WEBP file'}), 400

    return (content, content_type), None, None


def _get_uploaded_image_file():
    uploaded = request.files.get('image_file')
    if not uploaded:
        return None, jsonify({'error': 'image_file is required'}), 400

    content = uploaded.read(_MAX_RENDERED_FILE_BYTES + 1)
    if not content:
        return None, jsonify({'error': 'image_file is empty'}), 400
    if len(content) > _MAX_RENDERED_FILE_BYTES:
        return None, jsonify({'error': 'image_file is too large'}), 413

    content_type = (uploaded.mimetype or request.form.get('mime_type') or '').split(';')[0].strip().lower()
    if content_type not in _ALLOWED_RENDERED_MIME_TYPES:
        return None, jsonify({'error': 'Unsupported mime type'}), 400

    is_png = content.startswith(b'\x89PNG\r\n\x1a\n')
    is_jpeg = content.startswith(b'\xff\xd8')
    is_webp = content.startswith(b'RIFF') and len(content) > 12 and content[8:12] == b'WEBP'
    if content_type == 'image/png' and not is_png:
        return None, jsonify({'error': 'Invalid PNG file'}), 400
    if content_type in {'image/jpeg', 'image/jpg'} and not is_jpeg:
        return None, jsonify({'error': 'Invalid JPEG file'}), 400
    if content_type == 'image/webp' and not is_webp:
        return None, jsonify({'error': 'Invalid WEBP file'}), 400

    filename = secure_filename(uploaded.filename or '')
    return (content, content_type, filename), None, None


@bp.post('/api/geocache-images/<int:source_image_id>/edits')
def create_geocache_image_edit(source_image_id: int):
    source = GeocacheImage.query.get(source_image_id)
    if not source:
        return jsonify({'error': 'Image not found'}), 404

    root = _get_root_image(source)

    existing = GeocacheImage.query.filter_by(parent_image_id=root.id, derivation_type='edited').first()
    if existing is not None:
        return jsonify({'error': 'Edited image already exists', 'existing_image_id': existing.id}), 409

    upload, error_response, status_code = _get_uploaded_rendered_file()
    if error_response is not None:
        return error_response, status_code

    editor_state_json = request.form.get('editor_state_json')
    if editor_state_json is not None:
        if len(editor_state_json.encode('utf-8')) > _MAX_EDITOR_STATE_BYTES:
            return jsonify({'error': 'editor_state_json is too large'}), 413
        try:
            json.loads(editor_state_json)
        except json.JSONDecodeError:
            return jsonify({'error': 'editor_state_json must be valid JSON'}), 400
    title = request.form.get('title')

    content, content_type = upload

    derived = GeocacheImage(
        geocache_id=root.geocache_id,
        source_url=root.source_url,
        parent_image_id=root.id,
        derivation_type='edited',
        title=title,
        editor_state_json=editor_state_json,
        stored=False,
    )

    try:
        db.session.add(derived)
        db.session.flush()

        stored_path, mime_type, byte_size, sha256 = write_image_file(
            geocache_id=derived.geocache_id,
            image_id=derived.id,
            content=content,
            content_type=content_type,
            source_url=root.source_url,
        )

        derived.stored = True
        derived.stored_path = stored_path
        derived.mime_type = mime_type
        derived.byte_size = byte_size
        derived.sha256 = sha256

        db.session.commit()
        return jsonify(derived.to_dict())
    except Exception as exc:
        logger.error('Failed to create edit for image %s: %s', source_image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to create edited image'}), 500


@bp.post('/api/geocaches/<int:geocache_id>/images/upload')
def upload_geocache_image(geocache_id: int):
    geocache = Geocache.query.get(geocache_id)
    if not geocache:
        return jsonify({'error': 'Geocache not found'}), 404

    upload, error_response, status_code = _get_uploaded_image_file()
    if error_response is not None:
        return error_response, status_code

    title = request.form.get('title')
    note = request.form.get('note')

    content, content_type, filename = upload
    filename_part = filename or 'upload'
    pending_source_url = f'geoapp-upload://{geocache_id}/pending/{uuid.uuid4().hex}/{filename_part}'

    image = GeocacheImage(
        geocache_id=geocache_id,
        source_url=pending_source_url,
        derivation_type='manual',
        title=title,
        note=note,
        stored=False,
    )

    try:
        db.session.add(image)
        db.session.flush()

        image.source_url = f'geoapp-upload://{geocache_id}/{image.id}/{filename_part}'

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
        logger.error('Failed to upload image for geocache %s: %s', geocache_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to upload image'}), 500


@bp.post('/api/geocache-images/<int:source_image_id>/snippets/new')
def create_geocache_image_snippet_new(source_image_id: int):
    source = GeocacheImage.query.get(source_image_id)
    if not source:
        return jsonify({'error': 'Image not found'}), 404

    derivation_type = _next_derivation_type(source, source.id, 'snippet')

    upload, error_response, status_code = _get_uploaded_rendered_file()
    if error_response is not None:
        return error_response, status_code

    title = request.form.get('title')

    crop_rect_json = request.form.get('crop_rect_json')
    crop_rect = None
    if crop_rect_json is not None:
        if len(crop_rect_json.encode('utf-8')) > 16 * 1024:
            return jsonify({'error': 'crop_rect_json is too large'}), 413
        try:
            crop_rect = json.loads(crop_rect_json)
        except json.JSONDecodeError:
            return jsonify({'error': 'crop_rect_json must be valid JSON'}), 400
        if crop_rect is not None and not isinstance(crop_rect, dict):
            return jsonify({'error': 'crop_rect_json must be an object'}), 400

    content, content_type = upload

    derived = GeocacheImage(
        geocache_id=source.geocache_id,
        source_url=source.source_url,
        parent_image_id=source.id,
        derivation_type=derivation_type,
        title=title,
        crop_rect=crop_rect,
        stored=False,
    )

    try:
        db.session.add(derived)
        db.session.flush()

        stored_path, mime_type, byte_size, sha256 = write_image_file(
            geocache_id=derived.geocache_id,
            image_id=derived.id,
            content=content,
            content_type=content_type,
            source_url=source.source_url,
        )

        derived.stored = True
        derived.stored_path = stored_path
        derived.mime_type = mime_type
        derived.byte_size = byte_size
        derived.sha256 = sha256

        db.session.commit()
        return jsonify(derived.to_dict())
    except Exception as exc:
        logger.error('Failed to create snippet (new) for image %s: %s', source_image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to create snippet image'}), 500


@bp.post('/api/geocache-images/<int:source_image_id>/edits/new')
def create_geocache_image_edit_new(source_image_id: int):
    source = GeocacheImage.query.get(source_image_id)
    if not source:
        return jsonify({'error': 'Image not found'}), 404

    root = _get_root_image(source)
    derivation_type = _next_derivation_type(root, root.id, 'edited')

    upload, error_response, status_code = _get_uploaded_rendered_file()
    if error_response is not None:
        return error_response, status_code

    editor_state_json = request.form.get('editor_state_json')
    if editor_state_json is not None:
        if len(editor_state_json.encode('utf-8')) > _MAX_EDITOR_STATE_BYTES:
            return jsonify({'error': 'editor_state_json is too large'}), 413
        try:
            json.loads(editor_state_json)
        except json.JSONDecodeError:
            return jsonify({'error': 'editor_state_json must be valid JSON'}), 400
    title = request.form.get('title')

    content, content_type = upload

    derived = GeocacheImage(
        geocache_id=root.geocache_id,
        source_url=root.source_url,
        parent_image_id=root.id,
        derivation_type=derivation_type,
        title=title,
        editor_state_json=editor_state_json,
        stored=False,
    )

    try:
        db.session.add(derived)
        db.session.flush()

        stored_path, mime_type, byte_size, sha256 = write_image_file(
            geocache_id=derived.geocache_id,
            image_id=derived.id,
            content=content,
            content_type=content_type,
            source_url=root.source_url,
        )

        derived.stored = True
        derived.stored_path = stored_path
        derived.mime_type = mime_type
        derived.byte_size = byte_size
        derived.sha256 = sha256

        db.session.commit()
        return jsonify(derived.to_dict())
    except Exception as exc:
        logger.error('Failed to create edit (new) for image %s: %s', source_image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to create edited image'}), 500


@bp.put('/api/geocache-images/<int:image_id>/edits')
def update_geocache_image_edit(image_id: int):
    image = GeocacheImage.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    if not image.parent_image_id:
        return jsonify({'error': 'Only derived images can be overwritten'}), 400

    if not image.derivation_type or not image.derivation_type.startswith('edited'):
        return jsonify({'error': 'Only edited derived images can be overwritten'}), 400

    upload, error_response, status_code = _get_uploaded_rendered_file()
    if error_response is not None:
        return error_response, status_code

    editor_state_json = request.form.get('editor_state_json')
    if editor_state_json is not None:
        if len(editor_state_json.encode('utf-8')) > _MAX_EDITOR_STATE_BYTES:
            return jsonify({'error': 'editor_state_json is too large'}), 413
        try:
            json.loads(editor_state_json)
        except json.JSONDecodeError:
            return jsonify({'error': 'editor_state_json must be valid JSON'}), 400
    title = request.form.get('title')

    content, content_type = upload

    try:
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
        if title is not None:
            image.title = title
        if editor_state_json is not None:
            image.editor_state_json = editor_state_json

        db.session.commit()
        return jsonify(image.to_dict())
    except Exception as exc:
        logger.error('Failed to update edited image %s: %s', image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to update edited image'}), 500


@bp.post('/api/geocache-images/<int:image_id>/duplicate')
def duplicate_geocache_image(image_id: int):
    source = GeocacheImage.query.get(image_id)
    if not source:
        return jsonify({'error': 'Image not found'}), 404

    derivation_type = _next_derivation_type(source, source.id, 'copy')
    duplicated = GeocacheImage(
        geocache_id=source.geocache_id,
        source_url=source.source_url,
        parent_image_id=source.id,
        derivation_type=derivation_type,
        crop_rect=source.crop_rect,
        editor_state_json=source.editor_state_json,
        title=(f"{source.title} copy" if source.title else None),
        note=source.note,
        tags=source.tags,
        detected_features=source.detected_features,
        qr_payload=source.qr_payload,
        ocr_text=source.ocr_text,
        ocr_language=source.ocr_language,
        stored=False,
    )

    try:
        db.session.add(duplicated)
        db.session.flush()

        if source.stored and source.stored_path:
            try:
                file_path = _safe_resolve_stored_file(source.stored_path)
                content = file_path.read_bytes()
                content_type = (source.mime_type or 'image/png').split(';')[0].strip().lower()
                stored_path, mime_type, byte_size, sha256 = write_image_file(
                    geocache_id=duplicated.geocache_id,
                    image_id=duplicated.id,
                    content=content,
                    content_type=content_type,
                    source_url=source.source_url,
                )
                duplicated.stored = True
                duplicated.stored_path = stored_path
                duplicated.mime_type = mime_type
                duplicated.byte_size = byte_size
                duplicated.sha256 = sha256
            except Exception as exc:
                logger.warning('Failed to duplicate stored file for image %s: %s', image_id, exc)

        db.session.commit()
        return jsonify(duplicated.to_dict())
    except Exception as exc:
        logger.error('Failed to duplicate image %s: %s', image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to duplicate image'}), 500


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


@bp.post('/api/geocache-images/<int:image_id>/unstore')
def unstore_geocache_image(image_id: int):
    image = GeocacheImage.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    if not image.stored and not image.stored_path:
        return jsonify(image.to_dict())

    try:
        if image.stored_path:
            full_path = _safe_resolve_stored_file(image.stored_path)
            if full_path.exists() and full_path.is_file():
                full_path.unlink()

        image.stored = False
        image.stored_path = None
        image.mime_type = None
        image.byte_size = None
        image.sha256 = None

        db.session.commit()
        return jsonify(image.to_dict())
    except Exception as exc:
        logger.error('Failed to unstore image %s: %s', image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to unstore image'}), 500


@bp.delete('/api/geocache-images/<int:image_id>')
def delete_geocache_image(image_id: int):
    image = GeocacheImage.query.get(image_id)
    if not image:
        return jsonify({'error': 'Image not found'}), 404

    root = _get_root_image(image)
    if not (root.source_url or '').startswith('geoapp-upload://'):
        return jsonify({'error': 'Only uploaded images can be deleted'}), 400

    visited: set[int] = set()
    stack = [image]
    images_to_delete: list[GeocacheImage] = []

    while stack:
        current = stack.pop()
        if current.id in visited:
            continue
        visited.add(current.id)
        images_to_delete.append(current)
        children = GeocacheImage.query.filter_by(parent_image_id=current.id).all()
        stack.extend(children)

    try:
        for current in reversed(images_to_delete):
            if current.stored_path:
                try:
                    full_path = _safe_resolve_stored_file(current.stored_path)
                    if full_path.exists() and full_path.is_file():
                        full_path.unlink()
                except Exception as exc:
                    logger.warning('Failed to delete stored file for image %s: %s', current.id, exc)
            db.session.delete(current)

        db.session.commit()
        return jsonify({'deleted': sorted(visited)}), 200
    except Exception as exc:
        logger.error('Failed to delete image %s: %s', image_id, exc, exc_info=True)
        db.session.rollback()
        return jsonify({'error': 'Failed to delete image'}), 500


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
