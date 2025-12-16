"""
Tests pour le blueprint /api/preferences.
"""

import json
import sys
import types
import pytest

try:
    import pyproj  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - dépendance optionnelle pour les tests
    class _FakeGeod:
        def __init__(self, **_kwargs):
            pass

        def inv(self, *_args, **_kwargs):
            return 0.0, 0.0, 0.0

    sys.modules['pyproj'] = types.SimpleNamespace(Geod=_FakeGeod)

from gc_backend import create_app
from gc_backend.database import db


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


def test_get_preferences_returns_defaults(client):
    response = client.get('/api/preferences')
    assert response.status_code == 200
    payload = json.loads(response.data)
    assert 'preferences' in payload
    assert payload['preferences']['geoApp.plugins.lazyMode'] is True
    assert payload['preferences']['geoApp.checkers.certitudes.keepPageOpen'] is False
    assert payload['preferences']['geoApp.checkers.geocaching.keepPageOpen'] is False


def test_put_preference_updates_value(client):
    response = client.put('/api/preferences/geoApp.plugins.lazyMode', json={'value': False})
    assert response.status_code == 200

    response = client.get('/api/preferences/geoApp.plugins.lazyMode')
    assert response.status_code == 200
    payload = json.loads(response.data)
    assert payload['value'] is False


def test_put_unknown_preference_returns_404(client):
    response = client.put('/api/preferences/unknown.key', json={'value': 1})
    assert response.status_code == 404

