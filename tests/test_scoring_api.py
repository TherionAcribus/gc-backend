import json
from pathlib import Path

import pytest

from gc_backend import create_app
from gc_backend.database import db
from gc_backend.plugins.models import Plugin


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
def caesar_plugin(app):
    plugins_dir = Path(__file__).parent.parent / 'plugins'

    with app.app_context():
        caesar = Plugin(
            name='caesar',
            version='1.0.0',
            plugin_api_version='2.0',
            description='Caesar cipher plugin',
            author='MysterAI',
            plugin_type='python',
            source='official',
            path=str(plugins_dir / 'official' / 'caesar'),
            entry_point='main.py',
            enabled=True,
            metadata_json=json.dumps({
                "name": "caesar",
                "version": "1.0.0",
                "plugin_api_version": "2.0",
                "plugin_type": "python",
                "entry_point": "main.py",
                "enable_scoring": True
            })
        )
        db.session.add(caesar)
        db.session.commit()

        app.plugin_manager.discover_plugins()

    return caesar


class TestScoringEndpoint:
    def test_score_endpoint_gps_is_high(self, client):
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': "N 48° 33.787' E 006° 38.803'"
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert 'score' in data
        assert 'metadata' in data
        assert 'scoring' in data['metadata']
        assert data['metadata']['scoring']['features']['gps_confidence'] > 0.7
        assert data['score'] > 0.7

    def test_score_endpoint_quadgrams_features_present(self, client):
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'THIS THAT THERE WITH THEM'
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert 'metadata' in data
        assert 'scoring' in data['metadata']
        features = data['metadata']['scoring']['features']
        assert 'ngram_fitness' in features
        assert 'trigram_fitness' in features
        assert 'quadgram_fitness' in features
        assert 'repetition_quality' in features

        assert features['quadgram_fitness'] > 0.0
        assert features['repetition_quality'] == 1.0

    def test_score_endpoint_noise_is_low(self, client):
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'XJ12! FS QLM 0001110101010101'
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert 'score' in data
        assert data['score'] <= 0.2

    def test_score_endpoint_repetition_quality_penalizes(self, client):
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'AAAAAAAAAAAAAA'
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        assert features['repetition_quality'] == 0.0
        assert data['score'] < 0.7

    def test_score_endpoint_spelled_out_coords_are_not_flattened(self, client):
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'nord quarante six degres douze point cent trente est zero zero six trente deux point huit cinq six'
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        scoring = data['metadata']['scoring']
        features = scoring['features']

        assert features['coord_words'] >= 0.6
        assert scoring.get('early_exit') != 'ngram_low'
        assert data['score'] > 0.2

    def test_score_endpoint_spelled_out_coords_english_are_not_flattened(self, client):
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'north forty six degrees twelve point one three zero east zero zero six thirty two point eight five six'
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        scoring = data['metadata']['scoring']
        features = scoring['features']

        assert features['coord_words'] >= 0.6
        assert scoring.get('early_exit') != 'ngram_low'
        assert data['score'] > 0.2


class TestScoringIntegrationExecute:
    def test_execute_overwrites_confidence_and_keeps_plugin_confidence(self, client, caesar_plugin):
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip('Plugin Caesar non disponible')

        response = client.post(
            '/api/plugins/caesar/execute',
            data=json.dumps({
                'inputs': {
                    'text': 'HELLO',
                    'mode': 'encode',
                    'shift': 3,
                    'enable_scoring': True
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'ok'
        assert len(data['results']) == 1

        item = data['results'][0]
        assert item['text_output'] == 'KHOOR'

        assert 'metadata' in item
        assert 'plugin_confidence' in item['metadata']
        assert item['metadata']['plugin_confidence'] == 1.0

        assert 'confidence' in item
        assert isinstance(item['confidence'], (int, float))
        assert item['confidence'] != 1.0

        assert 'scoring' in item['metadata']
        assert 'score' in item['metadata']['scoring']

    def test_execute_detect_mode_neutralizes_confidence(self, client, caesar_plugin):
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip('Plugin Caesar non disponible')

        response = client.post(
            '/api/plugins/caesar/execute',
            data=json.dumps({
                'inputs': {
                    'text': 'SOMETHING',
                    'mode': 'detect',
                    'shift': 3,
                    'enable_scoring': True
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['status'] == 'ok'
        assert len(data['results']) == 1

        item = data['results'][0]
        assert item['confidence'] == 0.0
