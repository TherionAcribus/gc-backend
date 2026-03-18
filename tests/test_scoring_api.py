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


class TestScoringNumberRichness:
    """Tests for the number_richness and encoded_pattern features."""

    def test_number_words_fr_without_direction_scores_high(self, client):
        """Number words like 'vingt deux point quatre cent dix sept' should score high
        even without N/S/E/W direction signals."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'vingt deux point quatre cent dix sept'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        assert features['number_richness'] > 0.5
        assert data['score'] > 0.7

    def test_number_words_en_without_direction_scores_high(self, client):
        """English number words should also score high."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'twenty two point four hundred seventeen'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        assert features['number_richness'] > 0.5
        assert data['score'] > 0.7

    def test_hex_pairs_penalized_as_encoded(self, client):
        """Hex pair output like '76 69 6E 67 74 20...' should be heavily penalized."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': '76 69 6E 67 74 20 64 65 75 78 20 70 6N 69 6E 74 20 71 75 61 74 72 65 20 63 65 6E 74 20 64 69 78 20 78 65 70 74'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        assert features['encoded_penalty'] < 0.2
        assert data['score'] < 0.1

    def test_base64_penalized_as_encoded(self, client):
        """Base64-like strings should be penalized."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'SGVsbG8gV29ybGQhIFRoaXMgaXMgYSB0ZXN0'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        assert features['encoded_penalty'] <= 0.1
        assert data['score'] < 0.2

    def test_numeric_codes_penalized(self, client):
        """Sequences of short numeric codes separated by spaces should be penalized."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': '12 34 56 78 90 12 34 56 78 90'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['score'] < 0.15

    def test_number_richness_feature_present_in_metadata(self, client):
        """number_richness and encoded_penalty should appear in scoring features."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'vingt deux point quatre cent dix sept'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        assert 'number_richness' in features
        assert 'encoded_penalty' in features

    def test_coord_words_relaxation_with_separator(self, client):
        """coord_words should give partial credit for number words + separator
        even without direction signals."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'quarante huit virgule cinq cent douze'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        # coord_words should have partial credit via the relaxation path
        assert features['coord_words'] > 0.0

    def test_noise_with_binary_still_low(self, client):
        """Binary-like noise should not trigger number_richness."""
        response = client.post(
            '/api/plugins/score',
            data=json.dumps({
                'text': 'XJ12! FS QLM 0001110101010101'
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        features = data['metadata']['scoring']['features']

        assert features['number_richness'] == 0.0
        assert data['score'] <= 0.2


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

        # Encode mode now skips scoring — confidence is preserved as-is
        assert 'metadata' in item
        assert 'plugin_confidence' in item['metadata']
        assert item['metadata']['plugin_confidence'] == 1.0

        assert 'confidence' in item
        assert isinstance(item['confidence'], (int, float))
        assert item['confidence'] == 1.0  # encode results keep their original confidence

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
