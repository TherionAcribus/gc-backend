"""
Tests for the archive API.
"""

import sys
import types

import pytest

try:
    import pyproj  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - optional dependency in tests
    class _FakeGeod:
        def __init__(self, **_kwargs):
            pass

        def inv(self, *_args, **_kwargs):
            return 0.0, 0.0, 0.0

    sys.modules['pyproj'] = types.SimpleNamespace(Geod=_FakeGeod)

from gc_backend import create_app
from gc_backend.database import db
from gc_backend.models import Zone
from gc_backend.geocaches.archive_service import ArchiveService
from gc_backend.geocaches.models import Geocache


@pytest.fixture
def app():
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sample_geocache(app):
    with app.app_context():
        zone = Zone(name='Archive Test Zone')
        db.session.add(zone)
        db.session.flush()

        geocache = Geocache(
            gc_code='GCARCH1',
            name='Archive Diagnostic Cache',
            zone_id=zone.id,
            description_raw='Listing with hidden clues and a compact code.',
            hints='8 5 12 12 15',
            solved='in_progress',
        )
        db.session.add(geocache)
        db.session.commit()
        return geocache.gc_code


def test_update_resolution_diagnostics_creates_archive_from_geocache(client, app, sample_geocache):
    payload = {
        'source': 'plugin_executor_metasolver',
        'labels': [{'name': 'secret_code', 'confidence': 0.91}],
        'recommended_actions': ['Use metasolver on the best compact fragment.'],
        'metasolver': {
            'selected_plugins': ['alpha_decoder', 'morse_code']
        }
    }

    response = client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json=payload)

    assert response.status_code == 200
    assert response.get_json()['updated'] is True

    with app.app_context():
        archive = ArchiveService.get_by_gc_code(sample_geocache)
        assert archive is not None
        assert archive['gc_code'] == sample_geocache
        assert archive['solved_status'] == 'in_progress'
        assert archive['resolution_diagnostics']['source'] == 'plugin_executor_metasolver'
        assert archive['resolution_diagnostics']['metasolver']['selected_plugins'] == ['alpha_decoder', 'morse_code']


def test_update_resolution_diagnostics_returns_404_for_unknown_gc_code(client):
    response = client.put('/api/archive/GCUNKNOWN/resolution-diagnostics', json={'source': 'test'})

    assert response.status_code == 404
    assert response.get_json()['error'] == 'Archive not found for this gc_code'


def test_update_resolution_diagnostics_requires_payload(client, sample_geocache):
    response = client.put(f'/api/archive/{sample_geocache}/resolution-diagnostics', json={})

    assert response.status_code == 400
    assert response.get_json()['error'] == 'No data provided'
