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
        assert data['candidate_secret_fragments'][0]['signature']['looks_like_a1z26'] is True

    def test_classify_listing_requires_content(self, client, app):
        response = client.post(
            '/api/plugins/listing/classify',
            data=json.dumps({}),
            content_type='application/json'
        )

        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data


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
