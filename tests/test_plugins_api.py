"""
Tests pour les endpoints API des plugins.

Ces tests vérifient :
- Les routes de listage et informations
- L'exécution synchrone de plugins
- La génération d'interface HTML
- Les routes de gestion (discover, status, reload)
"""

import pytest
import json
from pathlib import Path

from gc_backend import create_app
from gc_backend.database import db
from gc_backend.models import Zone
from gc_backend.geocaches.models import Geocache, GeocacheWaypoint, GeocacheChecker
from gc_backend.plugins.models import Plugin
from .workflow_scenario_cases import REALISTIC_WORKFLOW_CASES


@pytest.fixture
def app():
    """Crée une instance de l'application pour les tests."""
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
    """Crée un client de test."""
    return app.test_client()

def _deep_copy_json_payload(value):
    return json.loads(json.dumps(value))


def _apply_realistic_scenario_mocks(app, monkeypatch, scenario):
    from gc_backend.blueprints import plugins as plugins_blueprint

    remote_css_map = scenario.get('remote_css_map') or {}
    if remote_css_map:
        normalized_map = {str(url): str(content) for url, content in remote_css_map.items()}
        monkeypatch.setattr(
            plugins_blueprint,
            '_fetch_remote_text',
            lambda url, timeout_sec=5, max_bytes=200_000: normalized_map.get(str(url), '')
        )

    mock_plugin_results = scenario.get('mock_plugin_results') or {}
    if mock_plugin_results:
        original_execute_plugin = app.plugin_manager.execute_plugin

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name in mock_plugin_results:
                return _deep_copy_json_payload(mock_plugin_results[plugin_name])
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

    mock_checker_result = scenario.get('mock_checker_result')
    if mock_checker_result:
        def fake_run_checker_with_target(**kwargs):
            result = _deep_copy_json_payload(mock_checker_result)
            result.setdefault('provider', kwargs.get('provider'))
            result.setdefault('url', kwargs.get('url'))
            result.setdefault('wp', kwargs.get('wp'))
            result.setdefault('interactive', kwargs.get('interactive'))
            result.setdefault('candidate', kwargs.get('candidate'))
            return result

        monkeypatch.setattr(plugins_blueprint, '_run_checker_with_target', fake_run_checker_with_target)



@pytest.fixture
def caesar_plugin(app):
    """
    Fixture qui crée et charge le plugin Caesar en DB.
    Nécessaire car TESTING=1 désactive la découverte automatique.
    """
    plugins_dir = Path(__file__).parent.parent / 'plugins'
    
    with app.app_context():
        # Créer le plugin en DB
        caesar = Plugin.query.filter_by(name='caesar').first()
        if caesar is None:
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
                enabled=True
            )
            db.session.add(caesar)
            db.session.commit()
        
        # Forcer la découverte pour charger le plugin dans le PluginManager
        app.plugin_manager.discover_plugins()
        
    return caesar


@pytest.fixture
def pi_digits_plugin(app):
    """
    Fixture qui crée et charge le plugin pi_digits en DB.
    Nécessaire car TESTING=1 désactive la découverte automatique.
    """
    plugins_dir = Path(__file__).parent.parent / 'plugins'

    with app.app_context():
        plugin = Plugin.query.filter_by(name='pi_digits').first()
        if plugin is None:
            plugin = Plugin(
                name='pi_digits',
                version='1.0.0',
                plugin_api_version='2.0',
                description='Pi digits decoder plugin',
                author='MysterAI',
                plugin_type='python',
                source='official',
                path=str(plugins_dir / 'official' / 'pi_digits'),
                entry_point='main.py',
                enabled=True
            )
            db.session.add(plugin)
            db.session.commit()

        app.plugin_manager.discover_plugins()

    return plugin


@pytest.fixture
def sample_geocache(app):
    with app.app_context():
        zone = Zone(name='Test Zone')
        db.session.add(zone)
        db.session.flush()

        geocache = Geocache(
            gc_code='GC99999',
            name='Hidden Formula Cache',
            zone_id=zone.id,
            description_raw='N 48 AB.CDE E 002 FG.HIJ\nA=8\nB=5\nC=12\nDecode the hidden code after solving.',
            description_html='''<div>N 48 AB.CDE E 002 FG.HIJ</div>
                <div>A=8 B=5 C=12</div>
                <!-- 8 5 12 12 15 -->
                <span style="display:none">.... . .-.. .-.. ---</span>''',
            hints='8 5 12 12 15',
            images=[{'url': 'https://example.test/puzzle.jpg'}],
        )
        db.session.add(geocache)
        db.session.flush()

        db.session.add(GeocacheWaypoint(
            geocache_id=geocache.id,
            prefix='PK',
            lookup='01',
            name='Projection',
            type='Final',
            gc_coords='N 48 AB.CDE E 002 FG.HIJ',
            note='Use waypoint projection once A, B and C are solved.'
        ))
        db.session.add(GeocacheChecker(
            geocache_id=geocache.id,
            name='Certitude',
            url='https://certitudes.org/certitude?wp=GC99999'
        ))
        db.session.commit()
        return {'id': geocache.id, 'gc_code': geocache.gc_code}


class TestPluginsListAPI:
    """Tests pour les endpoints de listage."""
    
    def test_list_all_plugins(self, client, app):
        """Test GET /api/plugins - liste tous les plugins."""
        response = client.get('/api/plugins')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'plugins' in data
        assert 'total' in data
        assert 'filters' in data
        assert isinstance(data['plugins'], list)
    
    def test_list_plugins_with_source_filter(self, client, app):
        """Test filtrage par source."""
        response = client.get('/api/plugins?source=official')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        # Vérifier que le filtre est appliqué
        assert data['filters']['source'] == 'official'
        
        # Tous les plugins doivent être official
        for plugin in data['plugins']:
            assert plugin['source'] == 'official'
    
    def test_list_plugins_with_category_filter(self, client, app):
        """Test filtrage par catégorie."""
        response = client.get('/api/plugins?category=Substitution')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['filters']['category'] == 'Substitution'
        
        # Tous les plugins doivent avoir la catégorie Substitution
        for plugin in data['plugins']:
            assert 'Substitution' in plugin.get('categories', [])
    
    def test_list_plugins_with_enabled_filter(self, client, app):
        """Test filtrage par statut enabled."""
        response = client.get('/api/plugins?enabled=true')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['filters']['enabled'] is True
        
        # Tous les plugins doivent être enabled
        for plugin in data['plugins']:
            assert plugin['enabled'] is True


class TestMetasolverRecommendationAPI:
    """Tests pour les endpoints d'assistance metasolver."""

    def test_list_metasolver_eligible_plugins(self, client, app, caesar_plugin):
        response = client.get('/api/plugins/metasolver/eligible?preset=letters_only')

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['preset'] == 'letters_only'
        assert 'plugins' in data
        assert isinstance(data['plugins'], list)
        assert any(plugin['name'] == 'caesar' for plugin in data['plugins'])

    def test_recommend_metasolver_plugins_for_morse(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '.... . .-.. .-.. ---',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_morse'] is True
        assert 'morse_code' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'morse_code'

    def test_recommend_metasolver_plugins_for_digits(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '8 5 12 12 15',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['dominant_input_kind'] == 'digits'
        assert data['signature']['looks_like_a1z26'] is True
        assert 'alpha_decoder' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'alpha_decoder'

    def test_recommend_metasolver_plugins_for_t9(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '43556',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_phone_keypad'] is True
        assert 't9_code' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 't9_code'

    def test_recommend_metasolver_plugins_for_pi_positions(self, client, app, pi_digits_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '19,44,25,64,41,51,87',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_pi_index_positions'] is True
        assert data['selected_plugins']
        assert data['selected_plugins'][0] == 'pi_digits'

    def test_recommend_metasolver_plugins_for_multitap(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '3 222 666 3 33',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_multitap'] is True
        assert 'multitap_code' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'multitap_code'

    def test_recommend_metasolver_plugins_for_houdini_words(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': 'Pray Answer Say',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['effective_preset'] == 'words_only'
        assert data['signature']['looks_like_houdini_words'] is True
        assert 'houdini_code' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'houdini_code'

    def test_recommend_metasolver_plugins_for_nak_nak(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': 'Nanak naknak Nak.',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['effective_preset'] == 'words_only'
        assert data['signature']['looks_like_nak_nak'] is True
        assert 'nak_nak_code' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'nak_nak_code'

    def test_recommend_metasolver_plugins_for_shadok(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': 'GA BU ZO MEU',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['effective_preset'] == 'words_only'
        assert data['signature']['looks_like_shadok'] is True
        assert 'shadok_numbers' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'shadok_numbers'

    def test_recommend_metasolver_plugins_for_tom_tom(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '/ // /\\\\ \\\\\\/',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['effective_preset'] == 'symbols_only'
        assert data['signature']['looks_like_tom_tom'] is True
        assert 'tom_tom' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'tom_tom'

    def test_recommend_metasolver_plugins_for_gold_bug(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '52-8*.$();',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['effective_preset'] == 'all'
        assert data['signature']['looks_like_gold_bug'] is True
        assert 'gold_bug' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'gold_bug'

    def test_recommend_metasolver_plugins_for_postnet(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '10001100101001100100101010010101',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['effective_preset'] == 'all'
        assert data['signature']['looks_like_postnet'] is True
        assert 'postnet_barcode' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'postnet_barcode'

    def test_recommend_metasolver_plugins_for_prime_numbers(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '2 3 5 7 11',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_prime_sequence'] is True
        assert 'prime_numbers' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'prime_numbers'

    def test_recommend_metasolver_plugins_for_chemical_elements(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': 'AU FE O',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_chemical_symbols'] is True
        assert 'chemical_elements' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'chemical_elements'

    def test_recommend_metasolver_plugins_for_chemical_elements_with_default_preset(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': 'AU FE O',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['effective_preset'] == 'words_only'
        assert data['signature']['looks_like_chemical_symbols'] is True
        assert data['recommendations'][0]['name'] == 'chemical_elements'

    def test_recommend_metasolver_plugins_for_bacon(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': 'AABBA ABBAA AABBA',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_bacon'] is True
        assert 'bacon_code' in data['selected_plugins']

    def test_recommend_metasolver_plugins_for_polybius(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': '11 21 34 34 44',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_polybius'] is True
        assert 'polybius_square' in data['selected_plugins']
        assert data['recommendations'][0]['name'] == 'polybius_square'

    def test_recommend_metasolver_plugins_for_roman(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({
                'text': 'XIV IX IV',
                'preset': 'all',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signature']['looks_like_roman_numerals'] is True
        assert 'roman_code' in data['selected_plugins']

    def test_recommend_metasolver_plugins_requires_text(self, client, app):
        response = client.post(
            '/api/plugins/metasolver/recommend',
            data=json.dumps({'text': '   '}),
            content_type='application/json'
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data


class TestListingClassificationAPI:
    """Tests pour la classification multi-label du listing."""

    def test_classify_listing_direct_input(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'Formula hidden code',
                'description_html': '<div>N 48 AB.CDE E 002 FG.HIJ</div><!-- 8 5 12 12 15 --><span style="display:none">secret</span>',
                'description': 'A=8 B=5 C=12. Solve the formula then decode the code.',
                'hint': '43556',
                'max_secret_fragments': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert {'formula', 'hidden_content', 'secret_code'}.issubset(labels)
        assert data['hidden_signals']
        assert data['candidate_secret_fragments']
        assert any(fragment['source'] == 'html_comment' for fragment in data['candidate_secret_fragments'])

    def test_classify_listing_detects_css_hidden_selector_content(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'Hidden CSS clue',
                'description': 'Inspect the page source before decoding.',
                'description_html': (
                    '<style>.secret-code{display:none}.ghost{color:transparent}</style>'
                    '<div>Visible text</div>'
                    '<span class="secret-code">8 5 12 12 15</span>'
                ),
                'max_secret_fragments': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert {'hidden_content', 'secret_code'}.issubset(labels)
        assert any('Hidden CSS selector' in signal for signal in data['hidden_signals'])
        assert data['signal_summary']['hidden_text_count'] >= 1
        assert any(fragment['source'] == 'hidden_css_text' for fragment in data['candidate_secret_fragments'])

    def test_classify_listing_detects_structural_css_hidden_selector_content(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'Hidden CSS structure clue',
                'description': 'Inspect the page source before decoding.',
                'description_html': (
                    '<style>.cloak .secret-code{display:none}</style>'
                    '<div class="cloak"><span class="secret-code">8 5 12 12 15</span></div>'
                ),
                'max_secret_fragments': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert {'hidden_content', 'secret_code'}.issubset(labels)
        assert any(item['source'] == 'hidden_css_text' for item in data['candidate_secret_fragments'])
        assert data['signal_summary']['best_secret_fragment_source'] == 'hidden_css_text'

    def test_classify_listing_detects_external_stylesheet_hidden_content(self, client, app, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint

        monkeypatch.setattr(
            plugins_blueprint,
            '_fetch_remote_text',
            lambda url, timeout_sec=5, max_bytes=200_000: '.cloak .secret-code{display:none}'
            if 'hidden.css' in str(url)
            else ''
        )

        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'Hidden external CSS clue',
                'description': 'Inspect the page source before decoding.',
                'description_html': (
                    '<link rel="stylesheet" href="https://example.test/hidden.css" />'
                    '<div class="cloak"><span class="secret-code">8 5 12 12 15</span></div>'
                ),
                'max_secret_fragments': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert {'hidden_content', 'secret_code'}.issubset(labels)
        assert any('external stylesheet' in signal for signal in data['hidden_signals'])
        assert any(fragment['source'] == 'hidden_css_text' for fragment in data['candidate_secret_fragments'])

    def test_classify_listing_image_puzzle(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'QR image puzzle',
                'description': 'Inspect the photo and scan the QR code hidden in the image.',
                'description_html': '<div><img src="https://example.test/qr.png" alt="qr clue" /></div>',
                'images': [{'url': 'https://example.test/qr.png'}]
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert 'image_puzzle' in labels
        assert data['signal_summary']['image_hint_count'] >= 1
        assert 'image_alt_text' in data['signal_summary']['image_hint_sources']

    def test_classify_listing_detects_visual_only_image_clue(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'ABCD',
                'description': 'Inspect the attached picture, compare the colored symbols, then rotate the image before decoding anything.',
                'description_html': '<div><img src="https://example.test/puzzle.png" /></div>',
                'images': [{'url': 'https://example.test/puzzle.png'}]
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert 'image_puzzle' in labels
        assert data['signal_summary']['visual_image_signal_count'] >= 2
        assert data['signal_summary']['has_visual_only_image_clue'] is True

    def test_classify_listing_ignores_geocaching_hex_image_filename(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'Pi',
                'description': 'E klenge Mystery passend zum Pi-Day N 19,44,25,64,41,51,87 E 50,77,20,32,69,66,60,32 Vill Spaass',
                'description_html': '<div><img src="https://img.geocaching.com/cache/large/94808f66-4460-47ab-a49f-b9d2de192528.jpg" /></div>',
                'images': [{'url': 'https://img.geocaching.com/cache/large/94808f66-4460-47ab-a49f-b9d2de192528.jpg'}],
                'checkers': [{'name': 'Geocaching', 'url': 'https://www.geocaching.com/play/checker.aspx?id=123456'}],
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert 'image_filename_text' not in set(data['signal_summary'].get('image_hint_sources') or [])
        assert all(fragment['source'] != 'image_filename_text' for fragment in data['candidate_secret_fragments'])
        assert data['signal_summary'].get('best_secret_fragment_source') != 'image_filename_text'

    def test_classify_listing_marks_hybrid_domains_when_image_dominates(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'ABCD',
                'description': 'Inspect the image and check the page source before decoding anything.',
                'description_html': '<div><img src="https://example.test/asset.png" alt="8 5 12 12 15" /></div><!-- hidden note -->',
                'images': [{'url': 'https://example.test/asset.png'}]
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert {'image_puzzle', 'hidden_content', 'secret_code'}.issubset(labels)
        assert data['signal_summary']['is_hybrid_listing'] is True
        assert data['signal_summary']['hybrid_domain_count'] >= 2
        assert data['signal_summary']['dominant_evidence_domain'] == 'image'
        assert data['signal_summary']['image_domain_score'] > data['signal_summary']['hidden_domain_score']
        assert data['signal_summary']['image_domain_score'] > data['signal_summary']['direct_domain_score']

    def test_classify_listing_marks_ambiguous_hybrid_domains(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'title': 'Hybrid clue',
                'description': 'Check the page source for clues before decoding anything.',
                'description_html': (
                    '<div><img src="https://example.test/img001.png" /></div>'
                    '<!-- note -->'
                ),
                'images': [{'url': 'https://example.test/img001.png'}]
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['signal_summary']['is_hybrid_listing'] is True
        assert data['signal_summary']['is_ambiguous_hybrid'] is True
        assert set(data['signal_summary']['ambiguous_domains']) >= {'hidden', 'image'}
        assert data['signal_summary']['evidence_domain_gap'] < 10.0

    def test_classify_listing_from_geocache(self, client, app, sample_geocache):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({
                'geocache_id': sample_geocache['id'],
                'max_secret_fragments': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert data['source'] == 'geocache'
        assert data['geocache']['gc_code'] == sample_geocache['gc_code']
        assert {'formula', 'hidden_content', 'secret_code', 'coord_transform', 'checker_available'}.issubset(labels)
        assert data['signal_summary']['checker_count'] == 1
        assert data['signal_summary']['image_count'] == 1
        assert any(
            fragment['signature']['looks_like_a1z26'] is True
            for fragment in data['candidate_secret_fragments']
        )

    def test_classify_listing_requires_content(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({}),
            content_type='application/json'
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data


class TestWorkflowOrchestratorAPI:
    """Tests pour l'orchestrateur de workflow GeoApp."""

    def test_resolve_workflow_for_secret_code(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Secret note',
                'description': 'Decode this note: 8 5 12 12 15',
                'hint': '8 5 12 12 15',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'secret_code'
        assert data['execution']['secret_code']['selected_fragment']['text'] == '8 5 12 12 15'
        assert 'alpha_decoder' in data['execution']['secret_code']['recommendation']['selected_plugins']
        assert any(step['id'] == 'recommend-metasolver-plugins' for step in data['plan'])

    def test_resolve_workflow_prefers_direct_pi_plugin(self, client, app, pi_digits_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Pi Mystery Cache',
                'description': (
                    'E klenge Mystery passend zum Pi-Day\n'
                    'N 19,44,25,64,41,51,87\n'
                    'E 50,77,20,32,69,66,60,32\n'
                    'Vill Spaass'
                ),
                'hint': 'Zntargvp',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'secret_code'
        assert data['classification']['signal_summary']['has_pi_theme'] is True
        assert data['classification']['signal_summary']['pi_position_token_count'] == 15
        assert any(step['id'] == 'execute-direct-plugin' for step in data['plan'])
        secret_execution = data['execution']['secret_code']
        assert secret_execution['direct_plugin_candidate'] is not None
        assert secret_execution['direct_plugin_candidate']['plugin_name'] == 'pi_digits'
        assert secret_execution['direct_plugin_candidate']['should_run_directly'] is True
        assert secret_execution['recommendation'] is not None
        assert 'pi_digits' in secret_execution['recommendation']['selected_plugins']

    def test_resolve_workflow_auto_executes_direct_pi_plugin(self, client, app, pi_digits_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Pi Mystery Cache',
                'description': (
                    'E klenge Mystery passend zum Pi-Day\n'
                    'N 19,44,25,64,41,51,87\n'
                    'E 50,77,20,32,69,66,60,32\n'
                    'Vill Spaass'
                ),
                'auto_execute': True,
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        secret_execution = data['execution']['secret_code']
        direct_result = secret_execution['direct_plugin_result']
        assert direct_result is not None
        assert direct_result['plugin_name'] == 'pi_digits'
        assert direct_result['status'] == 'success'
        assert direct_result['coordinates'] is not None
        assert direct_result['coordinates']['ddm'] == "N 49° 33.654' E 006° 06.740'"
        assert direct_result['top_results'][0]['text_output'] == "N 49° 33.654' E 006° 06.740'"

    def test_resolve_workflow_prefers_hidden_content_when_best_fragment_is_hidden(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Hidden note',
                'description': 'Inspect the page source before decoding.',
                'description_html': '<div>Visible text</div><!-- 8 5 12 12 15 --><span style="display:none">.... . .-.. .-.. ---</span>',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'hidden_content'
        assert 'HTML cache' in data['workflow']['reason'] or 'contenu cache' in data['workflow']['reason']
        candidate_kinds = [candidate['kind'] for candidate in data['workflow_candidates']]
        assert 'secret_code' in candidate_kinds
        assert 'hidden_content' in candidate_kinds
        assert data['classification']['signal_summary']['best_secret_fragment_source'] in {'html_comment', 'hidden_html_text'}

    def test_resolve_workflow_prefers_hidden_content_when_best_fragment_is_css_hidden(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Hidden CSS note',
                'description': 'Inspect the page source before decoding.',
                'description_html': (
                    '<style>.secret-code{display:none}</style>'
                    '<div>Visible text</div>'
                    '<span class="secret-code">8 5 12 12 15</span>'
                ),
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'hidden_content'
        assert any('Hidden CSS selector' in signal for signal in data['classification']['hidden_signals'])
        assert data['classification']['signal_summary']['best_secret_fragment_source'] == 'hidden_css_text'

    def test_resolve_workflow_prefers_hidden_content_in_hybrid_listing(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'ABCD',
                'description': 'Inspect the image and the page source before decoding anything.',
                'description_html': (
                    '<div><img src="https://example.test/asset.png" alt="clue" title="photo" /></div>'
                    '<!-- 8 5 12 12 15 -->'
                    '<span style="display:none">.... . .-.. .-.. ---</span>'
                ),
                'images': [{'url': 'https://example.test/asset.png'}],
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'hidden_content'
        assert data['classification']['signal_summary']['is_hybrid_listing'] is True
        assert data['classification']['signal_summary']['dominant_evidence_domain'] == 'hidden'
        candidate_kinds = [candidate['kind'] for candidate in data['workflow_candidates']]
        assert 'secret_code' in candidate_kinds
        assert 'hidden_content' in candidate_kinds
        assert 'image_puzzle' in candidate_kinds

    def test_resolve_workflow_adds_cross_domain_review_steps_for_ambiguous_hybrid(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Hybrid clue',
                'description': 'Check the page source for clues before decoding anything.',
                'description_html': (
                    '<div><img src="https://example.test/img001.png" /></div>'
                    '<!-- note -->'
                ),
                'images': [{'url': 'https://example.test/img001.png'}],
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['classification']['signal_summary']['is_ambiguous_hybrid'] is True
        assert set(data['classification']['signal_summary']['ambiguous_domains']) >= {'hidden', 'image'}
        plan_ids = [step['id'] for step in data['plan']]
        assert 'inspect-hidden-html' in plan_ids
        assert 'inspect-images' in plan_ids
        assert any('Listing hybride ambigu' in item for item in data['explanation'])
        assert any('Comparer les indices' in item for item in data['next_actions'])

    def test_resolve_workflow_prefers_image_puzzle_when_best_fragment_is_in_image(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Image clue',
                'description': 'Inspect the attached image before trying to decode anything.',
                'description_html': '<div><img src="https://example.test/8-5-12-12-15.png" alt="8 5 12 12 15" /></div>',
                'images': [{'url': 'https://example.test/8-5-12-12-15.png'}],
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'image_puzzle'
        assert 'indice image' in data['workflow']['reason'] or 'image' in data['workflow']['reason']
        candidate_kinds = [candidate['kind'] for candidate in data['workflow_candidates']]
        assert 'secret_code' in candidate_kinds
        assert 'image_puzzle' in candidate_kinds
        assert data['classification']['signal_summary']['best_secret_fragment_source'] in {
            'image_alt_text',
            'image_title_text',
            'image_filename_text',
        }

    def test_resolve_workflow_prefers_image_puzzle_for_visual_only_image_clue(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'ABCD',
                'description': 'Inspect the attached picture, compare the colored symbols, and rotate the image before trying to decode it.',
                'description_html': '<div><img src="https://example.test/puzzle.png" /></div>',
                'images': [{'url': 'https://example.test/puzzle.png'}],
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'image_puzzle'
        assert data['classification']['signal_summary']['has_visual_only_image_clue'] is True
        assert data['classification']['signal_summary']['best_secret_fragment_source'] == 'title'
        candidate_kinds = [candidate['kind'] for candidate in data['workflow_candidates']]
        assert 'secret_code' in candidate_kinds
        assert 'image_puzzle' in candidate_kinds

    def test_resolve_workflow_ignores_geocaching_hex_image_filename(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Pi',
                'description': 'E klenge Mystery passend zum Pi-Day N 19,44,25,64,41,51,87 E 50,77,20,32,69,66,60,32 Vill Spaass',
                'description_html': '<div><img src="https://img.geocaching.com/cache/large/94808f66-4460-47ab-a49f-b9d2de192528.jpg" /></div>',
                'images': [{'url': 'https://img.geocaching.com/cache/large/94808f66-4460-47ab-a49f-b9d2de192528.jpg'}],
                'checkers': [{'name': 'Geocaching', 'url': 'https://www.geocaching.com/play/checker.aspx?id=123456'}],
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['classification']['signal_summary']['best_secret_fragment_source'] != 'image_filename_text'
        assert data['workflow']['kind'] != 'image_puzzle'

    def test_resolve_workflow_for_formula(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Formula puzzle',
                'description': (
                    'A. Number of windows\n'
                    'B. Year built minus 1900\n'
                    'C. Number of benches\n'
                    'D. Number of arches\n'
                    'E. Number of steps\n'
                    'F. Number of stones\n'
                    'Coordinates: N 47° 59.ABC E 006° 12.DEF'
                ),
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'formula'
        assert data['execution']['formula']['formula_count'] >= 1
        assert 'A' in data['execution']['formula']['variables']
        assert data['execution']['formula']['found_question_count'] >= 1
        assert any(step['id'] == 'detect-formulas' for step in data['plan'])

    def test_resolve_workflow_prefers_formula_when_projection_clues_coexist(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Projection formula',
                'description': (
                    'Waypoint PARKING at N 47 59.123 E 006 12.345\n'
                    'A=8 B=5 C=12 D=15\n'
                    'Use the parking waypoint if needed.\n'
                    'Final coordinates: N 47 59.ABC E 006 12.DCB'
                ),
                'waypoints': [
                    {
                        'name': 'PARKING',
                        'latitude': 47.985383,
                        'longitude': 6.20575,
                        'note': 'Parking waypoint',
                    }
                ],
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'formula'
        candidate_by_kind = {candidate['kind']: candidate for candidate in data['workflow_candidates']}
        assert 'formula' in candidate_by_kind
        assert 'coord_transform' in candidate_by_kind
        assert candidate_by_kind['formula']['score'] > candidate_by_kind['coord_transform']['score']
        assert 'variable' in data['workflow']['reason'].lower() or 'formule' in data['workflow']['reason'].lower()

    def test_resolve_workflow_keeps_coord_transform_for_projection_only(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'title': 'Projection only',
                'description': (
                    'From the parking waypoint, project 315 m at bearing 120 degrees to reach the final coordinates. '
                    'Use the waypoint note and the offset to compute the final.'
                ),
                'waypoints': [
                    {
                        'name': 'PARKING',
                        'latitude': 47.985383,
                        'longitude': 6.20575,
                        'note': 'Parking waypoint',
                    }
                ],
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'coord_transform'
        candidate_by_kind = {candidate['kind']: candidate for candidate in data['workflow_candidates']}
        assert 'coord_transform' in candidate_by_kind
        assert 'formula' not in candidate_by_kind

    def test_resolve_workflow_honors_preferred_workflow(self, client, app, caesar_plugin, sample_geocache):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'geocache_id': sample_geocache['id'],
                'preferred_workflow': 'secret_code',
                'max_plugins': 5
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['workflow']['kind'] == 'secret_code'
        assert data['workflow']['forced'] is True
        assert data['execution']['secret_code']['recommendation'] is not None

    def test_resolve_workflow_can_execute_metasolver(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'description': '8 5 12 12 15',
                'preferred_workflow': 'secret_code',
                'auto_execute': True,
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        metasolver_result = data['execution']['secret_code']['metasolver_result']
        assert metasolver_result is not None
        assert metasolver_result['status'] in ('success', 'partial_success', 'ok')
        assert metasolver_result['results_count'] >= 1

    def test_resolve_workflow_returns_control_state(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps({
                'description': '8 5 12 12 15',
                'preferred_workflow': 'secret_code',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['control']['status'] == 'ready'
        assert data['control']['can_run_next_step'] is True
        assert data['control']['budget']['max_automated_steps'] >= 1
        assert data['control']['final_confidence'] >= 0.3


class TestRealisticWorkflowScenarioCorpus:
    """Tests parametres sur un petit corpus de scenarios proches du reel."""

    @pytest.mark.parametrize(
        'scenario',
        REALISTIC_WORKFLOW_CASES,
        ids=[case['id'] for case in REALISTIC_WORKFLOW_CASES],
    )
    def test_classify_listing_realistic_workflow_case(self, client, app, monkeypatch, scenario):
        _apply_realistic_scenario_mocks(app, monkeypatch, scenario)
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps(_deep_copy_json_payload(scenario['payload'])),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        labels = {item['name'] for item in data['labels']}
        assert set(scenario['expected_labels']).issubset(labels)
        assert isinstance(data['signal_summary'], dict)
        signal_expectations = scenario.get('expected_signal_summary') or {}
        if signal_expectations.get('is_hybrid_listing') is not None:
            assert data['signal_summary']['is_hybrid_listing'] is signal_expectations['is_hybrid_listing']
        if signal_expectations.get('is_ambiguous_hybrid') is not None:
            assert data['signal_summary']['is_ambiguous_hybrid'] is signal_expectations['is_ambiguous_hybrid']
        if signal_expectations.get('ambiguous_domains_contains'):
            assert set(data['signal_summary']['ambiguous_domains']) >= set(signal_expectations['ambiguous_domains_contains'])

    @pytest.mark.parametrize(
        'scenario',
        REALISTIC_WORKFLOW_CASES,
        ids=[case['id'] for case in REALISTIC_WORKFLOW_CASES],
    )
    def test_resolve_workflow_realistic_workflow_case(self, client, app, caesar_plugin, monkeypatch, scenario):
        _apply_realistic_scenario_mocks(app, monkeypatch, scenario)
        response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps(_deep_copy_json_payload(scenario['payload'])),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        if scenario.get('expected_workflow'):
            assert data['workflow']['kind'] == scenario['expected_workflow']
        plan_ids = [step['id'] for step in data['plan']]
        assert set(scenario['expected_plan_steps']).issubset(plan_ids)
        assert data['workflow']['reason']
        if scenario.get('expect_workflow_candidates', True):
            assert data['workflow_candidates']
        else:
            assert data['workflow_candidates'] == []
        signal_expectations = scenario.get('expected_signal_summary') or {}
        if signal_expectations.get('is_hybrid_listing') is not None:
            assert data['classification']['signal_summary']['is_hybrid_listing'] is signal_expectations['is_hybrid_listing']
        if signal_expectations.get('is_ambiguous_hybrid') is not None:
            assert data['classification']['signal_summary']['is_ambiguous_hybrid'] is signal_expectations['is_ambiguous_hybrid']
        if signal_expectations.get('ambiguous_domains_contains'):
            assert set(data['classification']['signal_summary']['ambiguous_domains']) >= set(signal_expectations['ambiguous_domains_contains'])



    @pytest.mark.parametrize(
        'scenario',
        [case for case in REALISTIC_WORKFLOW_CASES if case.get('step_runner')],
        ids=[case['id'] for case in REALISTIC_WORKFLOW_CASES if case.get('step_runner')],
    )
    def test_run_next_step_realistic_workflow_case(self, client, app, caesar_plugin, monkeypatch, scenario):
        _apply_realistic_scenario_mocks(app, monkeypatch, scenario)

        payload = _deep_copy_json_payload(scenario['payload'])
        resolve_response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert resolve_response.status_code == 200
        resolve_data = json.loads(resolve_response.data)

        step_runner = scenario['step_runner']
        run_response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                **payload,
                'preferred_workflow': resolve_data['workflow']['kind'],
                'target_step_id': step_runner['target_step_id'],
                'workflow_control': step_runner.get('workflow_control', resolve_data['control']),
                **(step_runner.get('request_overrides') or {}),
            }),
            content_type='application/json'
        )

        assert run_response.status_code == 200
        run_data = json.loads(run_response.data)

        expected_run_status = step_runner.get('expected_run_status', 'success')
        assert run_data['status'] == expected_run_status

        if expected_run_status == 'success':
            assert run_data['executed_step'] == step_runner['target_step_id']
            assert any(
                step['id'] == step_runner['target_step_id'] and step['status'] == 'completed'
                for step in run_data['workflow_resolution']['plan']
            )

        if step_runner.get('expected_message_contains'):
            assert step_runner['expected_message_contains'].lower() in str(run_data.get('message') or '').lower()

        if step_runner.get('expected_control_status'):
            assert run_data['workflow_resolution']['control']['status'] == step_runner['expected_control_status']

        execution = run_data['workflow_resolution']['execution'][step_runner['execution_branch']]
        if step_runner.get('expect_inspected'):
            assert execution['inspected'] is True
        if step_runner.get('expect_metasolver_result'):
            assert execution['metasolver_result'] is not None
        if step_runner.get('expected_selected_fragment_sources'):
            assert execution['selected_fragment']['source'] in set(step_runner['expected_selected_fragment_sources'])
        if step_runner.get('expected_recommendation_contains'):
            assert step_runner['expected_recommendation_contains'] in execution['recommendation']['selected_plugins']
        if step_runner.get('expected_checker_status'):
            assert execution['result']['status'] == step_runner['expected_checker_status']

class TestWorkflowStepRunnerAPI:
    """Tests pour l execution d une etape du workflow GeoApp."""

    def test_run_next_step_executes_metasolver(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'description': '8 5 12 12 15',
                'preferred_workflow': 'secret_code',
                'target_step_id': 'execute-metasolver',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'execute-metasolver'
        assert data['workflow_resolution']['execution']['secret_code']['metasolver_result'] is not None
        assert any(
            step['id'] == 'execute-metasolver' and step['status'] == 'completed'
            for step in data['workflow_resolution']['plan']
        )

    def test_run_next_step_executes_direct_pi_plugin(self, client, app, pi_digits_plugin):
        payload = {
            'title': 'Pi Mystery Cache',
            'description': (
                'E klenge Mystery passend zum Pi-Day\n'
                'N 19,44,25,64,41,51,87\n'
                'E 50,77,20,32,69,66,60,32\n'
                'Vill Spaass'
            ),
            'hint': 'Zntargvp',
            'max_plugins': 5,
        }

        resolve_response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert resolve_response.status_code == 200
        resolve_data = json.loads(resolve_response.data)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                **payload,
                'preferred_workflow': resolve_data['workflow']['kind'],
                'target_step_id': 'execute-direct-plugin',
                'workflow_control': resolve_data['control'],
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'execute-direct-plugin'
        secret_execution = data['workflow_resolution']['execution']['secret_code']
        assert secret_execution['direct_plugin_result'] is not None
        assert secret_execution['direct_plugin_result']['plugin_name'] == 'pi_digits'
        assert secret_execution['direct_plugin_result']['coordinates']['ddm'] == "N 49° 33.654' E 006° 06.740'"
        assert secret_execution['direct_plugin_result']['top_results'][0]['text_output'] == "N 49° 33.654' E 006° 06.740'"
        assert any(
            step['id'] == 'execute-direct-plugin' and step['status'] == 'completed'
            for step in data['workflow_resolution']['plan']
        )

    def test_run_next_step_inspects_hidden_html(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Hidden page',
                'description_html': '<div>Visible</div><!-- 8 5 12 12 15 --><span style="display:none">.... . .-.. .-.. ---</span>',
                'preferred_workflow': 'hidden_content',
                'target_step_id': 'inspect-hidden-html',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'inspect-hidden-html'
        hidden_execution = data['workflow_resolution']['execution']['hidden_content']
        assert hidden_execution['inspected'] is True
        assert 'HTML comments present' in hidden_execution['hidden_signals']
        assert any(item['source'] == 'html_comment' for item in hidden_execution['items'])
        assert any(item['source'] == 'hidden_html_text' for item in hidden_execution['items'])
        assert hidden_execution['candidate_secret_fragments']
        assert hidden_execution['selected_fragment'] is not None
        assert any(
            step['id'] == 'inspect-hidden-html' and step['status'] == 'completed'
            for step in data['workflow_resolution']['plan']
        )

    def test_run_next_step_inspects_css_hidden_html(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Hidden CSS page',
                'description_html': (
                    '<style>.secret-code{display:none}</style>'
                    '<div>Visible</div>'
                    '<span class="secret-code">8 5 12 12 15</span>'
                ),
                'preferred_workflow': 'hidden_content',
                'target_step_id': 'inspect-hidden-html',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        hidden_execution = data['workflow_resolution']['execution']['hidden_content']
        assert hidden_execution['inspected'] is True
        assert any(item['source'] == 'hidden_css_text' for item in hidden_execution['items'])
        assert '8 5 12 12 15' in hidden_execution['hidden_texts']
        assert hidden_execution['selected_fragment'] is not None

    def test_run_next_step_inspects_structural_css_hidden_html(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Hidden CSS structure page',
                'description_html': (
                    '<style>#wrapper > span.secret-code{display:none}</style>'
                    '<div id="wrapper"><span class="secret-code">8 5 12 12 15</span></div>'
                ),
                'preferred_workflow': 'hidden_content',
                'target_step_id': 'inspect-hidden-html',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        hidden_execution = data['workflow_resolution']['execution']['hidden_content']
        assert hidden_execution['inspected'] is True
        assert any(
            item['source'] == 'hidden_css_text'
            and '#wrapper > span.secret-code' in item['reason']
            for item in hidden_execution['items']
        )
        assert hidden_execution['selected_fragment'] is not None

    def test_run_next_step_inspects_external_css_hidden_html(self, client, app, caesar_plugin, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint

        monkeypatch.setattr(
            plugins_blueprint,
            '_fetch_remote_text',
            lambda url, timeout_sec=5, max_bytes=200_000: '.cloak .secret-code{display:none}'
            if 'hidden.css' in str(url)
            else ''
        )

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Hidden external CSS page',
                'description_html': (
                    '<link rel="stylesheet" href="https://example.test/hidden.css" />'
                    '<div class="cloak"><span class="secret-code">8 5 12 12 15</span></div>'
                ),
                'preferred_workflow': 'hidden_content',
                'target_step_id': 'inspect-hidden-html',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        hidden_execution = data['workflow_resolution']['execution']['hidden_content']
        assert hidden_execution['inspected'] is True
        assert any('external stylesheet' in signal for signal in hidden_execution['hidden_signals'])
        assert any(
            item['source'] == 'hidden_css_text'
            and 'external stylesheet' in item['reason']
            for item in hidden_execution['items']
        )
        assert hidden_execution['selected_fragment'] is not None

    def test_run_next_step_inspects_images(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Image clue',
                'description_html': '<div><img src="https://example.test/puzzle.png" alt="8 5 12 12 15" title="HELLO clue" /></div>',
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'inspect-images'
        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert image_execution['inspected'] is True
        assert image_execution['image_count'] == 1
        assert any(item['source'] == 'image_alt_text' for item in image_execution['items'])
        assert any(item['text'] == '8 5 12 12 15' for item in image_execution['items'])
        assert image_execution['candidate_secret_fragments']
        assert image_execution['selected_fragment'] is not None
        assert image_execution['recommendation'] is not None
        assert image_execution['recommendation']['selected_plugins']
        assert any(
            step['id'] == 'inspect-images' and step['status'] == 'completed'
            for step in data['workflow_resolution']['plan']
        )

    def test_run_next_step_extracts_filename_hints_from_images(self, client, app, caesar_plugin, monkeypatch):
        original_execute_plugin = app.plugin_manager.execute_plugin

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name in {'qr_code_detector', 'easyocr_ocr', 'vision_ocr'}:
                return {
                    'status': 'success',
                    'summary': '0 resultat',
                    'results': [],
                    'images_analyzed': 0,
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [{'url': 'https://example.test/finals/8-5-12-12-15.png'}],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4,
                'workflow_control': {
                    'budget': {
                        'max_automated_steps': 1,
                        'max_metasolver_runs': 0,
                        'max_search_questions': 0,
                        'max_checker_runs': 1,
                        'max_coordinate_calculations': 0,
                        'max_vision_ocr_runs': 0,
                        'stop_on_checker_success': True,
                    },
                    'usage': {
                        'automated_steps': 0,
                        'metasolver_runs': 0,
                        'search_questions': 0,
                        'checker_runs': 0,
                        'coordinate_calculations': 0,
                        'vision_ocr_runs': 0,
                    }
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert any(item['source'] == 'image_filename_text' for item in image_execution['items'])
        assert any(item['text'] == '8 5 12 12 15' for item in image_execution['items'])
        assert image_execution['selected_fragment'] is not None
        assert image_execution['recommendation'] is not None

    def test_run_next_step_inspects_explicit_images_without_geocache_id(self, client, app, caesar_plugin, monkeypatch):
        original_execute_plugin = app.plugin_manager.execute_plugin

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name == 'qr_code_detector':
                return {
                    'status': 'success',
                    'summary': '1 QR code detecte',
                    'results': [
                        {
                            'text_output': '8 5 12 12 15',
                            'image_url': 'https://example.test/qr-direct.png',
                            'confidence': 0.99,
                        }
                    ],
                }
            if plugin_name == 'easyocr_ocr':
                return {
                    'status': 'success',
                    'summary': '0 resultat OCR',
                    'results': [],
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [{'url': 'https://example.test/qr-direct.png'}],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'inspect-images'
        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert image_execution['inspected'] is True
        assert image_execution['image_count'] == 1
        assert any(item['source'] == 'image_qr_text' for item in image_execution['items'])
        assert image_execution['selected_fragment'] is not None
        assert image_execution['recommendation'] is not None
        assert image_execution['recommendation']['selected_plugins']

    def test_run_next_step_uses_vision_ocr_as_fallback(self, client, app, caesar_plugin, monkeypatch):
        original_execute_plugin = app.plugin_manager.execute_plugin

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name == 'qr_code_detector':
                return {
                    'status': 'success',
                    'summary': '0 QR code detecte',
                    'results': [],
                }
            if plugin_name == 'easyocr_ocr':
                return {
                    'status': 'success',
                    'summary': '0 resultat OCR',
                    'results': [],
                }
            if plugin_name == 'vision_ocr':
                return {
                    'status': 'success',
                    'summary': '1 resultat vision OCR',
                    'results': [
                        {
                            'text_output': '.... . .-.. .-.. ---',
                            'image_url': 'https://example.test/vision-only.png',
                            'confidence': 0.95,
                        }
                    ],
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [{'url': 'https://example.test/vision-only.png'}],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert any(item['source'] == 'image_vision_text' for item in image_execution['items'])
        assert any('vision_ocr' in summary for summary in image_execution['plugin_summaries'])
        assert image_execution['selected_fragment'] is not None
        assert data['workflow_resolution']['control']['usage']['vision_ocr_runs'] == 1
        assert data['workflow_resolution']['control']['remaining']['vision_ocr_runs'] == 2

    def test_run_next_step_limits_vision_ocr_to_remaining_image_budget(self, client, app, caesar_plugin, monkeypatch):
        original_execute_plugin = app.plugin_manager.execute_plugin
        vision_inputs_seen = []

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name == 'qr_code_detector':
                return {
                    'status': 'success',
                    'summary': '0 QR code detecte',
                    'results': [],
                }
            if plugin_name == 'easyocr_ocr':
                return {
                    'status': 'success',
                    'summary': '0 resultat OCR',
                    'results': [],
                }
            if plugin_name == 'vision_ocr':
                vision_inputs_seen.append(inputs)
                explicit_images = inputs.get('images') or []
                return {
                    'status': 'success',
                    'summary': f'{len(explicit_images)} resultat(s) vision OCR',
                    'results': [
                        {
                            'text_output': f'VISION {index + 1}',
                            'image_url': entry.get('url') if isinstance(entry, dict) else str(entry),
                            'confidence': 0.91,
                        }
                        for index, entry in enumerate(explicit_images)
                    ],
                    'images_analyzed': len(explicit_images),
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [
                    {'url': 'https://example.test/vision-a.png'},
                    {'url': 'https://example.test/vision-b.png'},
                    {'url': 'https://example.test/vision-c.png'},
                ],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4,
                'workflow_control': {
                    'budget': {
                        'max_automated_steps': 1,
                        'max_metasolver_runs': 0,
                        'max_search_questions': 0,
                        'max_checker_runs': 1,
                        'max_coordinate_calculations': 0,
                        'max_vision_ocr_runs': 2,
                        'stop_on_checker_success': True,
                    },
                    'usage': {
                        'automated_steps': 0,
                        'metasolver_runs': 0,
                        'search_questions': 0,
                        'checker_runs': 0,
                        'coordinate_calculations': 0,
                        'vision_ocr_runs': 0,
                    }
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert len(vision_inputs_seen) == 1
        assert len(vision_inputs_seen[0].get('images') or []) == 2
        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert image_execution['vision_ocr_images_analyzed'] == 2
        assert any('vision_ocr limited:' in summary for summary in image_execution['plugin_summaries'])
        assert data['workflow_resolution']['control']['usage']['vision_ocr_runs'] == 2
        assert data['workflow_resolution']['control']['remaining']['vision_ocr_runs'] == 0

    def test_run_next_step_accounts_vision_ocr_cost_by_image_size(self, client, app, caesar_plugin, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint

        original_execute_plugin = app.plugin_manager.execute_plugin
        vision_inputs_seen = []

        monkeypatch.setattr(
            plugins_blueprint,
            '_extract_image_metadata_items',
            lambda image_urls: {
                'items': [],
                'coordinate_candidates': [],
                'summaries': [],
                'image_details': [
                    {
                        'image_url': image_urls[0],
                        'width': 4200,
                        'height': 2800,
                        'byte_size': 5_200_000,
                    },
                    {
                        'image_url': image_urls[1],
                        'width': 900,
                        'height': 700,
                        'byte_size': 240_000,
                    },
                ],
            }
        )

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name == 'qr_code_detector':
                return {
                    'status': 'success',
                    'summary': '0 QR code detecte',
                    'results': [],
                }
            if plugin_name == 'easyocr_ocr':
                return {
                    'status': 'success',
                    'summary': '0 resultat OCR',
                    'results': [],
                }
            if plugin_name == 'vision_ocr':
                vision_inputs_seen.append(inputs)
                explicit_images = inputs.get('images') or []
                return {
                    'status': 'success',
                    'summary': f'{len(explicit_images)} resultat(s) vision OCR',
                    'results': [
                        {
                            'text_output': 'VISION LARGE IMAGE',
                            'image_url': explicit_images[0].get('url') if explicit_images else '',
                            'confidence': 0.93,
                        }
                    ],
                    'images_analyzed': len(explicit_images),
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [
                    {'url': 'https://example.test/huge-vision.png'},
                    {'url': 'https://example.test/small-vision.png'},
                ],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4,
                'workflow_control': {
                    'budget': {
                        'max_automated_steps': 1,
                        'max_metasolver_runs': 0,
                        'max_search_questions': 0,
                        'max_checker_runs': 1,
                        'max_coordinate_calculations': 0,
                        'max_vision_ocr_runs': 2,
                        'stop_on_checker_success': True,
                    },
                    'usage': {
                        'automated_steps': 0,
                        'metasolver_runs': 0,
                        'search_questions': 0,
                        'checker_runs': 0,
                        'coordinate_calculations': 0,
                        'vision_ocr_runs': 0,
                    }
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert len(vision_inputs_seen) == 1
        assert [entry.get('url') for entry in (vision_inputs_seen[0].get('images') or [])] == ['https://example.test/small-vision.png']
        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert image_execution['vision_ocr_images_analyzed'] == 1
        assert image_execution['vision_ocr_budget_cost'] == 1
        assert data['workflow_resolution']['control']['usage']['vision_ocr_runs'] == 1
        assert data['workflow_resolution']['control']['remaining']['vision_ocr_runs'] == 1

    def test_run_next_step_skips_vision_ocr_when_budget_is_zero(self, client, app, caesar_plugin, monkeypatch):
        original_execute_plugin = app.plugin_manager.execute_plugin
        vision_called = {'value': False}

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name == 'qr_code_detector':
                return {
                    'status': 'success',
                    'summary': '0 QR code detecte',
                    'results': [],
                }
            if plugin_name == 'easyocr_ocr':
                return {
                    'status': 'success',
                    'summary': '0 resultat OCR',
                    'results': [],
                }
            if plugin_name == 'vision_ocr':
                vision_called['value'] = True
                return {
                    'status': 'success',
                    'summary': '1 resultat vision OCR',
                    'results': [
                        {
                            'text_output': 'SHOULD NOT RUN',
                            'image_url': 'https://example.test/vision-budget-zero.png',
                            'confidence': 0.95,
                        }
                    ],
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [{'url': 'https://example.test/vision-budget-zero.png'}],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4,
                'workflow_control': {
                    'budget': {
                        'max_automated_steps': 1,
                        'max_metasolver_runs': 0,
                        'max_search_questions': 0,
                        'max_checker_runs': 1,
                        'max_coordinate_calculations': 0,
                        'max_vision_ocr_runs': 0,
                        'stop_on_checker_success': True,
                    },
                    'usage': {
                        'automated_steps': 0,
                        'metasolver_runs': 0,
                        'search_questions': 0,
                        'checker_runs': 0,
                        'coordinate_calculations': 0,
                        'vision_ocr_runs': 0,
                    }
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert vision_called['value'] is False
        assert not any(item['source'] == 'image_vision_text' for item in image_execution['items'])
        assert any('vision_ocr skipped:' in summary for summary in image_execution['plugin_summaries'])
        assert data['workflow_resolution']['control']['usage']['vision_ocr_runs'] == 0
        assert data['workflow_resolution']['control']['remaining']['vision_ocr_runs'] == 0

    def test_run_next_step_registers_barcode_results(self, client, app, caesar_plugin, monkeypatch):
        original_execute_plugin = app.plugin_manager.execute_plugin

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name == 'qr_code_detector':
                return {
                    'status': 'success',
                    'summary': '1 barcode detecte',
                    'results': [
                        {
                            'text_output': '1234567890',
                            'barcode_type': 'CODE128',
                            'image_url': 'https://example.test/barcode.png',
                            'confidence': 1.0,
                        }
                    ],
                }
            if plugin_name in {'easyocr_ocr', 'vision_ocr'}:
                return {
                    'status': 'success',
                    'summary': '0 resultat',
                    'results': [],
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [{'url': 'https://example.test/barcode.png'}],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert any(item['source'] == 'image_barcode_text' for item in image_execution['items'])

    def test_run_next_step_uses_exif_image_metadata(self, client, app, caesar_plugin, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint

        monkeypatch.setattr(
            plugins_blueprint,
            '_extract_image_metadata_items',
            lambda image_urls: {
                'items': [
                    {
                        'source': 'image_exif_text',
                        'reason': 'EXIF ImageDescription',
                        'text': 'HELLO FROM EXIF',
                        'image_url': image_urls[0] if image_urls else 'https://example.test/exif.jpg',
                        'confidence': 0.9,
                    }
                ],
                'coordinate_candidates': [
                    {
                        'source': 'image_exif_gps',
                        'image_url': image_urls[0] if image_urls else 'https://example.test/exif.jpg',
                        'confidence': 0.93,
                        'coordinates': {'latitude': 48.1234, 'longitude': 2.1234, 'decimal': '48.1234, 2.1234'},
                    }
                ],
                'summaries': ['EXIF: 2 indice(s) extrait(s)'],
            }
        )

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'images': [{'url': 'https://example.test/exif.jpg'}],
                'preferred_workflow': 'image_puzzle',
                'target_step_id': 'inspect-images',
                'max_plugins': 4
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        image_execution = data['workflow_resolution']['execution']['image_puzzle']
        assert any(item['source'] == 'image_exif_text' for item in image_execution['items'])
        assert any('EXIF:' in summary for summary in image_execution['plugin_summaries'])
        assert image_execution['coordinates_candidate'] is not None

    def test_run_next_step_blocks_when_budget_is_exhausted(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'description': '8 5 12 12 15',
                'preferred_workflow': 'secret_code',
                'target_step_id': 'execute-metasolver',
                'workflow_control': {
                    'budget': {
                        'max_automated_steps': 1,
                        'max_metasolver_runs': 1,
                        'max_search_questions': 0,
                        'max_checker_runs': 1,
                        'max_coordinate_calculations': 0,
                        'stop_on_checker_success': True
                    },
                    'usage': {
                        'automated_steps': 1,
                        'metasolver_runs': 1,
                        'search_questions': 0,
                        'checker_runs': 0,
                        'coordinate_calculations': 0
                    }
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'blocked'
        assert 'budget' in data['message'].lower() or 'epuise' in data['message'].lower()
        assert data['workflow_resolution']['control']['status'] == 'budget_exhausted'

    def test_run_next_step_searches_formula_answers(self, client, app, caesar_plugin, monkeypatch):
        from gc_backend.services.web_search_service import web_search_service

        monkeypatch.setattr(web_search_service, 'search', lambda question, context=None, max_results=5: [
            {
                'text': f'Resultat pour {question}',
                'source': 'https://example.test/search',
                'score': 0.9,
                'type': 'mock'
            }
        ])
        monkeypatch.setattr(web_search_service, 'extract_answer', lambda results: '42')

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Formula puzzle',
                'description': (
                    'A. Number of windows\n'
                    'B. Year built minus 1900\n'
                    'C. Number of benches\n'
                    'Coordinates: N 47° 59.ABC E 006° 12.CBA'
                ),
                'preferred_workflow': 'formula',
                'target_step_id': 'search-answers'
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'search-answers'
        answer_search = data['workflow_resolution']['execution']['formula']['answer_search']
        assert answer_search['found_count'] >= 1
        assert answer_search['answers']['A']['best_answer'] == '42'
        assert answer_search['answers']['A']['recommended_value_type'] in ('value', 'length', 'checksum', 'reduced_checksum')

    def test_run_next_step_limits_formula_search_to_budget(self, client, app, caesar_plugin, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint
        from gc_backend.services.web_search_service import web_search_service

        monkeypatch.setattr(web_search_service, 'search', lambda question, context=None, max_results=5: [
            {
                'text': f'Resultat pour {question}',
                'source': 'https://example.test/search',
                'score': 0.9,
                'type': 'mock'
            }
        ])
        monkeypatch.setattr(web_search_service, 'extract_answer', lambda results: '42')
        monkeypatch.setattr(
            plugins_blueprint,
            '_resolve_workflow_orchestrator',
            lambda data, max_secret_fragments=6, max_plugins=8, auto_execute=False: {
                'source': 'direct_input',
                'geocache': None,
                'title': 'Formula puzzle',
                'workflow': {
                    'kind': 'formula',
                    'confidence': 0.8,
                    'score': 0.82,
                    'reason': 'Workflow formule force pour le test.',
                    'supporting_labels': ['formula'],
                    'forced': True,
                },
                'workflow_candidates': [],
                'classification': {
                    'source': 'direct_input',
                    'geocache': None,
                    'title': 'Formula puzzle',
                    'max_secret_fragments': 6,
                    'labels': [{
                        'name': 'formula',
                        'confidence': 0.8,
                        'evidence': ['Coordinate formula placeholders detected'],
                        'suggested_next_step': 'List variables and coordinate placeholders, then use the formula solver workflow.',
                    }],
                    'recommended_actions': [],
                    'candidate_secret_fragments': [],
                    'hidden_signals': [],
                    'formula_signals': ['Coordinate formula placeholders detected'],
                    'signal_summary': {
                        'has_title': True,
                        'has_hint': False,
                        'has_description_html': False,
                        'image_count': 0,
                        'checker_count': 0,
                        'waypoint_count': 0,
                    },
                },
                'plan': [
                    {'id': 'classify-listing', 'title': 'Classifier le listing', 'status': 'completed', 'automated': True},
                    {'id': 'choose-workflow', 'title': 'Selectionner le workflow principal: formula', 'status': 'completed', 'automated': True},
                    {'id': 'detect-formulas', 'title': 'Detecter les formules de coordonnees', 'status': 'completed', 'automated': True},
                    {'id': 'extract-questions', 'title': 'Associer les questions aux variables', 'status': 'completed', 'automated': True},
                    {'id': 'search-answers', 'title': 'Chercher les reponses factuelles manquantes', 'status': 'planned', 'automated': False, 'tool': 'formula-solver.search-answer'},
                    {'id': 'calculate-final-coordinates', 'title': 'Calculer les coordonnees finales', 'status': 'planned', 'automated': False, 'tool': 'formula-solver.calculate-coordinates'},
                ],
                'execution': {
                    'secret_code': None,
                    'formula': {
                        'formula_count': 1,
                        'formulas': [{'north': 'N 47 59.ABC', 'east': 'E 006 12.CBA'}],
                        'variables': ['A', 'B', 'C'],
                        'questions': {
                            'A': 'Number of windows',
                            'B': 'Year built minus 1900',
                            'C': 'Number of benches',
                        },
                        'found_question_count': 3,
                    },
                    'checker': None,
                },
                'control': {
                    'status': 'ready',
                    'budget': {
                        'max_automated_steps': 3,
                        'max_metasolver_runs': 0,
                        'max_search_questions': 1,
                        'max_checker_runs': 1,
                        'max_coordinate_calculations': 1,
                        'stop_on_checker_success': True,
                    },
                    'usage': {
                        'automated_steps': 0,
                        'metasolver_runs': 0,
                        'search_questions': 0,
                        'checker_runs': 0,
                        'coordinate_calculations': 0,
                    },
                    'remaining': {
                        'automated_steps': 3,
                        'metasolver_runs': 0,
                        'search_questions': 1,
                        'checker_runs': 1,
                        'coordinate_calculations': 1,
                    },
                    'stop_reasons': [],
                    'can_run_next_step': True,
                    'requires_user_input': False,
                    'final_confidence': 0.7,
                    'summary': 'Des etapes automatisees restent executables.',
                },
                'next_actions': ['Chercher les reponses factuelles manquantes', 'Calculer les coordonnees finales'],
                'explanation': ['Workflow principal: formula (0.80)'],
            }
        )

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Formula puzzle',
                'description': (
                    'A. Number of windows\n'
                    'B. Year built minus 1900\n'
                    'C. Number of benches\n'
                    'Coordinates: N 47° 59.ABC E 006° 12.CBA'
                ),
                'preferred_workflow': 'formula',
                'target_step_id': 'search-answers',
                'workflow_control': {
                    'budget': {
                        'max_automated_steps': 3,
                        'max_metasolver_runs': 0,
                        'max_search_questions': 1,
                        'max_checker_runs': 1,
                        'max_coordinate_calculations': 1,
                        'stop_on_checker_success': True
                    },
                    'usage': {
                        'automated_steps': 0,
                        'metasolver_runs': 0,
                        'search_questions': 0,
                        'checker_runs': 0,
                        'coordinate_calculations': 0
                    }
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        answer_search = data['workflow_resolution']['execution']['formula']['answer_search']
        assert len(answer_search['answers']) == 1
        assert data['workflow_resolution']['control']['usage']['search_questions'] == 1

    def test_run_next_step_calculates_formula_coordinates(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Formula puzzle',
                'description': (
                    'A. Number of windows\n'
                    'B. Year built minus 1900\n'
                    'C. Number of benches\n'
                    'Coordinates: N 47° 59.ABC E 006° 12.CBA'
                ),
                'preferred_workflow': 'formula',
                'target_step_id': 'calculate-final-coordinates',
                'formula_values': {
                    'A': 1,
                    'B': 2,
                    'C': 3
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'calculate-final-coordinates'
        calculation = data['workflow_resolution']['execution']['formula']['calculated_coordinates']
        assert calculation['status'] == 'success'
        assert 'coordinates' in calculation
        assert calculation['coordinates']['ddm']
        assert any(
            step['id'] == 'calculate-final-coordinates' and step['status'] == 'completed'
            for step in data['workflow_resolution']['plan']
        )

    def test_run_next_step_calculates_formula_coordinates_with_geographic_plausibility(self, client, app, caesar_plugin):
        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'title': 'Formula puzzle',
                'description': (
                    'A. Number of windows\n'
                    'B. Year built minus 1900\n'
                    'C. Number of benches\n'
                    'Coordinates: N 47А 59.ABC E 006А 12.CBA'
                ),
                'waypoints': [{
                    'name': 'Final area',
                    'latitude': 47.98538333,
                    'longitude': 6.20535,
                    'gc_coords': 'N 47° 59.123 E 006° 12.321',
                }],
                'preferred_workflow': 'formula',
                'target_step_id': 'calculate-final-coordinates',
                'formula_values': {
                    'A': 1,
                    'B': 2,
                    'C': 3
                }
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        calculation = data['workflow_resolution']['execution']['formula']['calculated_coordinates']
        plausibility = calculation['geographic_plausibility']
        assert plausibility['status'] in ('very_plausible', 'plausible')
        assert plausibility['nearest_reference']['type'] == 'waypoint'
        assert plausibility['nearest_reference']['distance_km'] <= 0.1
        assert data['workflow_resolution']['control']['final_confidence'] >= 0.88

    def test_run_next_step_validates_with_checker(self, client, app, caesar_plugin, sample_geocache, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint

        def fake_run_checker_with_target(**kwargs):
            assert kwargs['provider'] == 'certitudes'
            assert 'certitudes.org' in kwargs['url']
            assert kwargs['candidate'] == 'N 48 12.345 E 002 34.567'
            return {
                'status': 'success',
                'provider': kwargs['provider'],
                'url': kwargs['url'],
                'wp': kwargs['wp'],
                'interactive': True,
                'candidate': kwargs['candidate'],
                'result': {
                    'status': 'success',
                    'message': 'Checker accepted the candidate',
                    'evidence': 'Felicitation'
                }
            }

        monkeypatch.setattr(plugins_blueprint, '_run_checker_with_target', fake_run_checker_with_target)

        response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                'geocache_id': sample_geocache['id'],
                'target_step_id': 'validate-with-checker',
                'checker_candidate': 'N 48 12.345 E 002 34.567'
            }),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)

        assert data['status'] == 'success'
        assert data['executed_step'] == 'validate-with-checker'
        checker_execution = data['workflow_resolution']['execution']['checker']
        assert checker_execution['provider'] == 'certitudes'
        assert checker_execution['result']['status'] == 'success'
        assert data['workflow_resolution']['control']['status'] == 'stopped'
        assert data['workflow_resolution']['control']['can_run_next_step'] is False
        assert any(
            step['id'] == 'validate-with-checker' and step['status'] == 'completed'
            for step in data['workflow_resolution']['plan']
        )


class TestWorkflowEndToEndScenarios:
    """Scenarios end-to-end proches d'un usage reel."""

    def test_end_to_end_hidden_hybrid_external_css_flow(self, client, app, caesar_plugin, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint

        monkeypatch.setattr(
            plugins_blueprint,
            '_fetch_remote_text',
            lambda url, timeout_sec=5, max_bytes=200_000: '.cloak .secret-code{display:none}'
            if 'hidden.css' in str(url)
            else ''
        )

        payload = {
            'title': 'Hybrid hidden note',
            'description': 'Inspect the image and the page source before decoding anything.',
            'description_html': (
                '<link rel="stylesheet" href="https://example.test/hidden.css" />'
                '<div class="cloak"><span class="secret-code">8 5 12 12 15</span></div>'
                '<div><img src="https://example.test/photo.png" alt="photo clue" /></div>'
            ),
            'images': [{'url': 'https://example.test/photo.png'}],
            'max_plugins': 4,
        }

        resolve_response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert resolve_response.status_code == 200
        resolve_data = json.loads(resolve_response.data)

        assert resolve_data['workflow']['kind'] == 'hidden_content'
        assert any(step['id'] == 'inspect-hidden-html' for step in resolve_data['plan'])
        assert resolve_data['classification']['signal_summary']['best_secret_fragment_source'] == 'hidden_css_text'

        run_response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                **payload,
                'preferred_workflow': resolve_data['workflow']['kind'],
                'target_step_id': 'inspect-hidden-html',
                'workflow_control': resolve_data['control'],
            }),
            content_type='application/json'
        )

        assert run_response.status_code == 200
        run_data = json.loads(run_response.data)

        assert run_data['status'] == 'success'
        hidden_execution = run_data['workflow_resolution']['execution']['hidden_content']
        assert hidden_execution['selected_fragment']['source'] == 'hidden_css_text'
        assert '8 5 12 12 15' in hidden_execution['selected_fragment']['text']
        assert 'alpha_decoder' in hidden_execution['recommendation']['selected_plugins']
        assert run_data['workflow_resolution']['control']['usage']['automated_steps'] == 1

    def test_end_to_end_image_qr_flow(self, client, app, caesar_plugin, monkeypatch):
        original_execute_plugin = app.plugin_manager.execute_plugin

        def fake_execute_plugin(plugin_name, inputs):
            if plugin_name == 'qr_code_detector':
                return {
                    'status': 'success',
                    'summary': '1 QR code detecte',
                    'results': [
                        {
                            'text_output': '8 5 12 12 15',
                            'image_url': 'https://example.test/final-qr.png',
                            'confidence': 0.99,
                        }
                    ],
                }
            if plugin_name in {'easyocr_ocr', 'vision_ocr'}:
                return {
                    'status': 'success',
                    'summary': '0 resultat',
                    'results': [],
                }
            return original_execute_plugin(plugin_name, inputs)

        monkeypatch.setattr(app.plugin_manager, 'execute_plugin', fake_execute_plugin)

        payload = {
            'title': 'Visual image clue',
            'description': 'Inspect the image, compare symbols, and scan it before decoding anything.',
            'description_html': '<div><img src="https://example.test/final-qr.png" /></div>',
            'images': [{'url': 'https://example.test/final-qr.png'}],
            'max_plugins': 4,
        }

        resolve_response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert resolve_response.status_code == 200
        resolve_data = json.loads(resolve_response.data)

        assert resolve_data['workflow']['kind'] == 'image_puzzle'
        assert any(step['id'] == 'inspect-images' for step in resolve_data['plan'])

        run_response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                **payload,
                'preferred_workflow': resolve_data['workflow']['kind'],
                'target_step_id': 'inspect-images',
                'workflow_control': resolve_data['control'],
            }),
            content_type='application/json'
        )

        assert run_response.status_code == 200
        run_data = json.loads(run_response.data)

        assert run_data['status'] == 'success'
        image_execution = run_data['workflow_resolution']['execution']['image_puzzle']
        assert image_execution['selected_fragment']['source'] == 'image_qr_text'
        assert 'alpha_decoder' in image_execution['recommendation']['selected_plugins']
        assert run_data['workflow_resolution']['control']['usage']['automated_steps'] == 1

    def test_end_to_end_formula_search_calculate_checker_flow(self, client, app, caesar_plugin, monkeypatch):
        from gc_backend.blueprints import plugins as plugins_blueprint
        from gc_backend.services.web_search_service import web_search_service

        def fake_search(question, context=None, max_results=5):
            normalized = str(question or '').lower()
            if 'windows' in normalized:
                answer = '1'
            elif 'doors' in normalized:
                answer = '2'
            else:
                answer = '3'
            return [{
                'text': f'Answer {answer} for {question}',
                'answer': answer,
                'source': 'https://example.test/search',
                'score': 0.95,
                'type': 'mock',
            }]

        monkeypatch.setattr(web_search_service, 'search', fake_search)
        monkeypatch.setattr(web_search_service, 'extract_answer', lambda results: str((results or [{}])[0].get('answer') or ''))

        checker_calls = []

        def fake_run_checker_with_target(**kwargs):
            checker_calls.append(kwargs)
            return {
                'status': 'success',
                'provider': kwargs['provider'],
                'url': kwargs['url'],
                'wp': kwargs.get('wp'),
                'interactive': kwargs.get('interactive'),
                'candidate': kwargs['candidate'],
                'result': {
                    'status': 'success',
                    'message': 'Checker accepted the candidate',
                    'evidence': 'Felicitation',
                }
            }

        monkeypatch.setattr(plugins_blueprint, '_run_checker_with_target', fake_run_checker_with_target)

        payload = {
            'title': 'Museum formula',
            'description': (
                'A. Number of windows\n'
                'B. Number of doors\n'
                'C. Number of chimneys\n'
                'Coordinates: N 47° 59.ABC E 006° 12.CBA'
            ),
            'waypoints': [{
                'name': 'Final area',
                'latitude': 47.98538333,
                'longitude': 6.20535,
                'gc_coords': 'N 47° 59.123 E 006° 12.321',
            }],
            'checkers': [{
                'name': 'Certitude',
                'url': 'https://certitudes.org/certitude?wp=GC11111',
            }],
        }

        resolve_response = client.post(
            '/api/plugins/workflow/resolve',
            data=json.dumps(payload),
            content_type='application/json'
        )

        assert resolve_response.status_code == 200
        resolve_data = json.loads(resolve_response.data)

        assert resolve_data['workflow']['kind'] == 'formula'
        assert any(step['id'] == 'search-answers' for step in resolve_data['plan'])
        assert any(step['id'] == 'calculate-final-coordinates' for step in resolve_data['plan'])
        assert any(step['id'] == 'validate-with-checker' for step in resolve_data['plan'])

        search_response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                **payload,
                'preferred_workflow': resolve_data['workflow']['kind'],
                'target_step_id': 'search-answers',
                'workflow_control': resolve_data['control'],
            }),
            content_type='application/json'
        )

        assert search_response.status_code == 200
        search_data = json.loads(search_response.data)
        assert search_data['status'] == 'success'
        answer_search = search_data['workflow_resolution']['execution']['formula']['answer_search']
        assert answer_search['found_count'] == 3

        formula_values = {
            variable: int(details['best_answer'])
            for variable, details in (answer_search.get('answers') or {}).items()
        }
        assert formula_values == {'A': 1, 'B': 2, 'C': 3}

        calculate_response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                **payload,
                'preferred_workflow': 'formula',
                'target_step_id': 'calculate-final-coordinates',
                'formula_values': formula_values,
                'workflow_control': search_data['workflow_resolution']['control'],
            }),
            content_type='application/json'
        )

        assert calculate_response.status_code == 200
        calculate_data = json.loads(calculate_response.data)
        assert calculate_data['status'] == 'success'
        calculation = calculate_data['workflow_resolution']['execution']['formula']['calculated_coordinates']
        assert calculation['status'] == 'success'
        assert calculation['coordinates']['ddm'] == 'N 47° 59.123 E 006° 12.321'
        assert calculation['geographic_plausibility']['status'] in ('very_plausible', 'plausible')

        checker_candidate = calculation['coordinates']['ddm']
        checker_response = client.post(
            '/api/plugins/workflow/run-next-step',
            data=json.dumps({
                **payload,
                'preferred_workflow': 'formula',
                'target_step_id': 'validate-with-checker',
                'checker_candidate': checker_candidate,
                'workflow_control': calculate_data['workflow_resolution']['control'],
            }),
            content_type='application/json'
        )

        assert checker_response.status_code == 200
        checker_data = json.loads(checker_response.data)

        assert checker_data['status'] == 'success'
        checker_execution = checker_data['workflow_resolution']['execution']['checker']
        assert checker_execution['status'] == 'success'
        assert checker_execution['candidate'] == 'N 47° 59.123 E 006° 12.321'
        assert checker_calls and checker_calls[0]['candidate'] == 'N 47° 59.123 E 006° 12.321'
        assert checker_data['workflow_resolution']['control']['status'] in ('stopped', 'completed')


class TestPluginInfoAPI:
    """Tests pour l'endpoint d'informations d'un plugin."""
    
    def test_get_plugin_info_success(self, client, app, caesar_plugin):
        """Test GET /api/plugins/<name> - plugin existant."""
        # Vérifier si Caesar existe
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        response = client.get('/api/plugins/caesar')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['name'] == 'caesar'
        assert 'version' in data
        assert 'description' in data
        assert 'metadata' in data
    
    def test_get_plugin_info_not_found(self, client, app):
        """Test GET /api/plugins/<name> - plugin inexistant."""
        response = client.get('/api/plugins/nonexistent_plugin')
        
        assert response.status_code == 404
        data = json.loads(response.data)
        
        assert 'error' in data
        assert data['plugin_name'] == 'nonexistent_plugin'


class TestPluginInterfaceAPI:
    """Tests pour la génération d'interface HTML."""
    
    def test_get_plugin_interface_success(self, client, app, caesar_plugin):
        """Test GET /api/plugins/<name>/interface - génération HTML."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        response = client.get('/api/plugins/caesar/interface')
        
        assert response.status_code == 200
        assert response.content_type.startswith('text/html')
        
        # Vérifier que l'HTML contient des éléments attendus
        html = response.data.decode('utf-8')
        assert 'caesar' in html.lower()
        assert '<form' in html
        assert 'input' in html or 'select' in html
    
    def test_get_plugin_interface_not_found(self, client, app):
        """Test génération interface pour plugin inexistant."""
        response = client.get('/api/plugins/nonexistent/interface')
        
        assert response.status_code == 404


class TestPluginExecuteAPI:
    """Tests pour l'exécution synchrone de plugins."""
    
    def test_execute_plugin_success(self, client, app, caesar_plugin):
        """Test POST /api/plugins/<name>/execute - exécution réussie."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        response = client.post(
            '/api/plugins/caesar/execute',
            data=json.dumps({
                'inputs': {
                    'text': 'HELLO',
                    'mode': 'encode',
                    'shift': 3
                }
            }),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'ok'
        assert 'results' in data
        assert len(data['results']) > 0
        assert data['results'][0]['text_output'] == 'KHOOR'
    
    def test_execute_plugin_missing_inputs(self, client, app):
        """Test exécution sans inputs."""
        response = client.post(
            '/api/plugins/caesar/execute',
            data=json.dumps({}),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        
        assert 'error' in data
        assert 'inputs' in data['message'].lower()
    
    def test_execute_plugin_invalid_json(self, client, app):
        """Test exécution avec JSON invalide."""
        response = client.post(
            '/api/plugins/caesar/execute',
            data='invalid json',
            content_type='application/json'
        )
        
        # Flask retourne 400 pour JSON invalide
        assert response.status_code in [400, 415]
    
    def test_execute_plugin_bruteforce(self, client, app, caesar_plugin):
        """Test exécution en mode bruteforce."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        response = client.post(
            '/api/plugins/caesar/execute',
            data=json.dumps({
                'inputs': {
                    'text': 'URYYB',
                    'mode': 'decode',
                    'brute_force': True
                }
            }),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['status'] == 'ok'
        # Doit retourner 25 résultats (ROT-1 à ROT-25)
        assert len(data['results']) == 25


class TestPluginManagementAPI:
    """Tests pour les endpoints de gestion."""
    
    def test_discover_plugins(self, client, app):
        """Test POST /api/plugins/discover."""
        response = client.post('/api/plugins/discover')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'discovered' in data
        assert 'plugins' in data
        assert 'errors' in data
        assert 'message' in data
        assert isinstance(data['discovered'], int)
    
    def test_get_plugins_status(self, client, app):
        """Test GET /api/plugins/status."""
        response = client.get('/api/plugins/status')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'plugins' in data
        assert 'total' in data
        assert 'loaded' in data
        assert 'enabled' in data
        
        # Vérifier structure des infos de statut
        if len(data['plugins']) > 0:
            first_plugin = next(iter(data['plugins'].values()))
            assert 'enabled' in first_plugin
            assert 'loaded' in first_plugin
    
    def test_reload_plugin_success(self, client, app, caesar_plugin):
        """Test POST /api/plugins/<name>/reload - rechargement réussi."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # D'abord charger le plugin en l'exécutant
        client.post(
            '/api/plugins/caesar/execute',
            data=json.dumps({
                'inputs': {
                    'text': 'TEST',
                    'mode': 'encode',
                    'shift': 1
                }
            }),
            content_type='application/json'
        )
        
        # Puis recharger
        response = client.post('/api/plugins/caesar/reload')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['success'] is True
        assert 'caesar' in data['message']
    
    def test_reload_plugin_not_found(self, client, app):
        """Test rechargement d'un plugin inexistant."""
        response = client.post('/api/plugins/nonexistent/reload')
        
        # Le rechargement échouera mais ne doit pas planter
        assert response.status_code in [200, 500]


class TestPluginAPIIntegration:
    """Tests d'intégration complets des API."""
    
    def test_full_workflow(self, client, app):
        """Test workflow complet : discover → list → info → execute."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # 1. Découvrir les plugins
        response = client.post('/api/plugins/discover')
        assert response.status_code == 200
        discover_data = json.loads(response.data)
        assert discover_data['discovered'] > 0
        
        # 2. Lister les plugins
        response = client.get('/api/plugins')
        assert response.status_code == 200
        list_data = json.loads(response.data)
        assert len(list_data['plugins']) > 0
        
        # 3. Récupérer infos Caesar
        response = client.get('/api/plugins/caesar')
        assert response.status_code == 200
        info_data = json.loads(response.data)
        assert info_data['name'] == 'caesar'
        
        # 4. Exécuter Caesar
        response = client.post(
            '/api/plugins/caesar/execute',
            data=json.dumps({
                'inputs': {
                    'text': 'ABC',
                    'mode': 'encode',
                    'shift': 1
                }
            }),
            content_type='application/json'
        )
        assert response.status_code == 200
        exec_data = json.loads(response.data)
        assert exec_data['status'] == 'ok'
        assert exec_data['results'][0]['text_output'] == 'BCD'
        
        # 5. Vérifier le statut
        response = client.get('/api/plugins/status')
        assert response.status_code == 200
        status_data = json.loads(response.data)
        
        # Caesar doit être loaded maintenant
        assert 'caesar' in status_data['plugins']
        assert status_data['plugins']['caesar']['loaded'] is True


