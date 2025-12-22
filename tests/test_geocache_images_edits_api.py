"""
Tests pour les endpoints d'édition d'images de géocaches.

Ces tests vérifient :
- Lecture de l'état d'édition
- Création d'une image dérivée (non-destructive editing)
- Mise à jour d'une image dérivée existante
- Validations (mimetype, JSON, overwrite interdit sur l'original, conflit dérivée existante)

Les tests isolent l'écriture disque en patchant le répertoire de stockage.
"""

from __future__ import annotations

import io
import json

import pytest

from gc_backend import create_app
from gc_backend.database import db
from gc_backend.geocaches.models import Geocache, GeocacheImage
from gc_backend.models import Zone


@pytest.fixture
def app(tmp_path):
    """Crée une instance de l'application Flask pour les tests."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    # Isoler le stockage disque des images vers un dossier temporaire.
    import gc_backend.geocaches.image_storage as image_storage

    def _tmp_images_root_dir():
        return tmp_path / 'geocache_images'

    image_storage.get_images_root_dir = _tmp_images_root_dir  # type: ignore[assignment]

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seed_data(app):
    """Crée une zone, une geocache et une image originale en base."""
    with app.app_context():
        zone = Zone(name='Z1', description='test')
        db.session.add(zone)
        db.session.flush()

        geocache = Geocache(
            gc_code='GC_TEST',
            name='Test',
            zone_id=zone.id,
        )
        db.session.add(geocache)
        db.session.flush()

        original = GeocacheImage(
            geocache_id=geocache.id,
            source_url='https://example.invalid/image.jpg',
            derivation_type='original',
            stored=False,
        )
        db.session.add(original)
        db.session.commit()

        return {
            'zone_id': zone.id,
            'geocache_id': geocache.id,
            'original_image_id': original.id,
        }


def _make_png_bytes() -> bytes:
    # PNG signature + minimal IHDR chunk bytes (not a full valid PNG but passes signature sniffing)
    return b'\x89PNG\r\n\x1a\n' + b'0' * 64


class TestGeocacheImageEditorState:
    def test_get_editor_state_returns_null_when_missing(self, client, seed_data):
        response = client.get(f"/api/geocache-images/{seed_data['original_image_id']}/editor-state")
        assert response.status_code == 200
        payload = json.loads(response.data)
        assert 'editor_state_json' in payload
        assert payload['editor_state_json'] is None


class TestGeocacheImageEdits:
    def test_create_edit_creates_derived_image(self, client, app, seed_data):
        response = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': []}),
                'title': 'edited',
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )

        assert response.status_code == 200
        payload = json.loads(response.data)
        assert payload['parent_image_id'] == seed_data['original_image_id']
        assert payload['derivation_type'] == 'edited'
        assert payload['stored'] is True

        with app.app_context():
            derived = GeocacheImage.query.get(payload['id'])
            assert derived is not None
            assert derived.parent_image_id == seed_data['original_image_id']
            assert derived.editor_state_json is not None

    def test_create_edit_conflict_when_already_exists(self, client, seed_data):
        resp1 = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': []}),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': []}),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp2.status_code == 409
        payload = json.loads(resp2.data)
        assert payload['error'] == 'Edited image already exists'
        assert 'existing_image_id' in payload

    def test_create_edit_new_always_creates_new_variant(self, client, app, seed_data):
        resp1 = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': []}),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp1.status_code == 200

        resp2 = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits/new",
            data={
                'editor_state_json': json.dumps({'objects': [{'type': 'circle'}]}),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp2.status_code == 200
        payload2 = json.loads(resp2.data)
        assert payload2['parent_image_id'] == seed_data['original_image_id']
        assert payload2['derivation_type'].startswith('edited')

        with app.app_context():
            derived2 = GeocacheImage.query.get(payload2['id'])
            assert derived2 is not None
            assert derived2.derivation_type.startswith('edited')
            assert derived2.parent_image_id == seed_data['original_image_id']

    def test_duplicate_creates_copy_variant(self, client, app, seed_data):
        create_resp = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': []}),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert create_resp.status_code == 200
        derived_id = json.loads(create_resp.data)['id']

        dup_resp = client.post(f"/api/geocache-images/{derived_id}/duplicate")
        assert dup_resp.status_code == 200
        payload = json.loads(dup_resp.data)
        assert payload['parent_image_id'] == derived_id
        assert payload['derivation_type'].startswith('copy')

        with app.app_context():
            duplicated = GeocacheImage.query.get(payload['id'])
            assert duplicated is not None
            assert duplicated.parent_image_id == derived_id
            assert duplicated.derivation_type.startswith('copy')

    def test_create_snippet_new_creates_variant_and_stores_crop_rect(self, client, app, seed_data):
        crop1 = {'left': 10, 'top': 12, 'width': 80, 'height': 50}
        resp1 = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/snippets/new",
            data={
                'crop_rect_json': json.dumps(crop1),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'snippet.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp1.status_code == 200
        payload1 = json.loads(resp1.data)
        assert payload1['parent_image_id'] == seed_data['original_image_id']
        assert payload1['derivation_type'].startswith('snippet')

        crop2 = {'left': 0, 'top': 0, 'width': 12, 'height': 12}
        resp2 = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/snippets/new",
            data={
                'crop_rect_json': json.dumps(crop2),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'snippet.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp2.status_code == 200
        payload2 = json.loads(resp2.data)
        assert payload2['parent_image_id'] == seed_data['original_image_id']
        assert payload2['derivation_type'].startswith('snippet')
        assert payload2['id'] != payload1['id']

        with app.app_context():
            img1 = GeocacheImage.query.get(payload1['id'])
            img2 = GeocacheImage.query.get(payload2['id'])
            assert img1 is not None
            assert img2 is not None
            assert img1.crop_rect == crop1
            assert img2.crop_rect == crop2

    def test_update_edit_updates_existing_derived_image(self, client, app, seed_data):
        resp1 = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': [{'type': 'rect'}]}),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp1.status_code == 200
        derived_id = json.loads(resp1.data)['id']

        resp2 = client.put(
            f"/api/geocache-images/{derived_id}/edits",
            data={
                'editor_state_json': json.dumps({'objects': [{'type': 'circle'}]}),
                'title': 'new title',
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert resp2.status_code == 200

        with app.app_context():
            derived = GeocacheImage.query.get(derived_id)
            assert derived is not None
            assert derived.title == 'new title'
            assert derived.editor_state_json == json.dumps({'objects': [{'type': 'circle'}]})

    def test_update_edit_rejects_original(self, client, seed_data):
        response = client.put(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': []}),
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 400

    def test_create_edit_rejects_invalid_editor_state_json(self, client, seed_data):
        response = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': '{not-valid-json',
                'rendered_file': (io.BytesIO(_make_png_bytes()), 'render.png', 'image/png'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        payload = json.loads(response.data)
        assert payload['error'] == 'editor_state_json must be valid JSON'

    def test_create_edit_rejects_unsupported_mime_type(self, client, seed_data):
        response = client.post(
            f"/api/geocache-images/{seed_data['original_image_id']}/edits",
            data={
                'editor_state_json': json.dumps({'objects': []}),
                'rendered_file': (io.BytesIO(b'not an image'), 'render.txt', 'text/plain'),
            },
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        payload = json.loads(response.data)
        assert payload['error'] == 'Unsupported mime type'
