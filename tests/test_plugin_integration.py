"""
Tests d'intégration pour le système de plugins complet.

Ces tests vérifient le fonctionnement de bout en bout :
- Découverte de plugins
- Enregistrement en base de données
- Chargement lazy
- Exécution
- Résultats au format standardisé
"""

import pytest
import json
from pathlib import Path

from gc_backend.plugins.plugin_manager import PluginManager
from gc_backend.plugins.models import Plugin
from gc_backend import create_app
from gc_backend.database import db


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


class TestPluginIntegration:
    """Tests d'intégration du système complet de plugins."""
    
    def test_discover_and_execute_caesar(self, app):
        """
        Test complet : découverte du plugin Caesar et exécution.
        
        Ce test vérifie :
        1. La découverte du plugin Caesar dans plugins/official/
        2. L'enregistrement correct en base de données
        3. Le chargement lazy du plugin
        4. L'exécution avec différents modes
        5. Le format de sortie standardisé
        """
        # Chemin vers les plugins
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        # Créer le PluginManager
        manager = PluginManager(str(plugins_dir), app)
        
        # 1. Découvrir les plugins
        discovered = manager.discover_plugins()
        
        # Vérifier que Caesar a été découvert
        caesar_found = any(p['name'] == 'caesar' for p in discovered)
        assert caesar_found, "Plugin Caesar non découvert"
        
        # 2. Vérifier en base de données
        with app.app_context():
            caesar_db = Plugin.query.filter_by(name='caesar').first()
            
            assert caesar_db is not None
            assert caesar_db.version == '1.0.0'
            assert caesar_db.plugin_type == 'python'
            assert caesar_db.source == 'official'
            assert 'Substitution' in caesar_db.categories
            assert caesar_db.enabled is True
        
        # 3. Exécuter le plugin - Mode encode
        result_encode = manager.execute_plugin('caesar', {
            'text': 'HELLO',
            'mode': 'encode',
            'shift': 3
        })
        
        assert result_encode is not None
        assert result_encode['status'] == 'ok'
        assert len(result_encode['results']) == 1
        assert result_encode['results'][0]['text_output'] == 'KHOOR'
        assert result_encode['plugin_info']['name'] == 'caesar'
        
        # 4. Exécuter le plugin - Mode decode
        result_decode = manager.execute_plugin('caesar', {
            'text': 'KHOOR',
            'mode': 'decode',
            'shift': 3
        })
        
        assert result_decode is not None
        assert result_decode['status'] == 'ok'
        assert result_decode['results'][0]['text_output'] == 'HELLO'
        
        # 5. Vérifier que le plugin est en cache
        assert 'caesar' in manager.loaded_plugins
    
    def test_bruteforce_mode(self, app):
        """Test du mode bruteforce du plugin Caesar."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Exécuter en mode bruteforce
        result = manager.execute_plugin('caesar', {
            'text': 'URYYB',  # HELLO avec ROT-13
            'mode': 'decode',
            'brute_force': True
        })
        
        assert result is not None
        assert result['status'] == 'ok'
        
        # Doit retourner 25 résultats (ROT-1 à ROT-25)
        assert len(result['results']) == 25
        
        # Vérifier que ROT-13 donne bien HELLO
        rot13_result = next(
            (r for r in result['results'] 
             if r['parameters']['shift'] == 13),
            None
        )
        
        assert rot13_result is not None
        assert rot13_result['text_output'] == 'HELLO'
    
    def test_plugin_reload(self, app):
        """Test du rechargement d'un plugin."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Charger le plugin
        plugin1 = manager.get_plugin('caesar')
        assert plugin1 is not None
        
        # Recharger le plugin
        result = manager.reload_plugin('caesar')
        assert result is True
        
        # Vérifier qu'une nouvelle instance a été créée
        plugin2 = manager.get_plugin('caesar')
        assert plugin2 is not None
        # Les instances doivent être différentes
        assert plugin1 is not plugin2
    
    def test_plugin_status(self, app):
        """Test de récupération du statut des plugins."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Charger un plugin
        manager.get_plugin('caesar')
        
        # Récupérer le statut
        status = manager.get_plugin_status()
        
        assert 'caesar' in status
        assert status['caesar']['enabled'] is True
        assert status['caesar']['loaded'] is True
        assert status['caesar']['source'] == 'official'
        assert status['caesar']['plugin_type'] == 'python'
    
    def test_list_plugins_filters(self, app):
        """Test des filtres de listage de plugins."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Liste tous les plugins
        all_plugins = manager.list_plugins()
        assert len(all_plugins) > 0
        
        # Filtrer par source
        official_plugins = manager.list_plugins(source='official')
        assert all(p['source'] == 'official' for p in official_plugins)
        
        # Filtrer par catégorie
        substitution_plugins = manager.list_plugins(category='Substitution')
        assert any(p['name'] == 'caesar' for p in substitution_plugins)
    
    def test_plugin_execution_error_handling(self, app):
        """Test de la gestion d'erreur lors de l'exécution."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Essayer d'exécuter un plugin inexistant
        result = manager.execute_plugin('nonexistent_plugin', {})
        
        assert result is not None
        assert result['status'] == 'error'
        assert 'non disponible' in result['summary']
    
    def test_lazy_loading(self, app):
        """Test que les plugins sont chargés de manière lazy."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Après découverte, aucun plugin ne devrait être chargé
        assert len(manager.loaded_plugins) == 0
        
        # Exécuter un plugin
        manager.execute_plugin('caesar', {'text': 'TEST', 'mode': 'encode'})
        
        # Maintenant, le plugin devrait être chargé
        assert len(manager.loaded_plugins) == 1
        assert 'caesar' in manager.loaded_plugins
    
    def test_unload_plugin(self, app):
        """Test du déchargement d'un plugin."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Charger le plugin
        manager.get_plugin('caesar')
        assert 'caesar' in manager.loaded_plugins
        
        # Décharger le plugin
        result = manager.unload_plugin('caesar')
        assert result is True
        assert 'caesar' not in manager.loaded_plugins
    
    def test_unload_all_plugins(self, app):
        """Test du déchargement de tous les plugins."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        # Charger plusieurs plugins
        manager.get_plugin('caesar')
        # Charger d'autres plugins si disponibles
        
        initial_count = len(manager.loaded_plugins)
        assert initial_count > 0
        
        # Décharger tous
        manager.unload_all_plugins()
        
        assert len(manager.loaded_plugins) == 0


class TestCaesarPluginFormats:
    """Tests spécifiques au plugin Caesar pour vérifier le format de sortie."""
    
    def test_output_format_encode(self, app):
        """Vérifie le format de sortie en mode encode."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        result = manager.execute_plugin('caesar', {
            'text': 'ABC',
            'mode': 'encode',
            'shift': 1
        })
        
        # Vérifier structure
        assert 'status' in result
        assert 'summary' in result
        assert 'results' in result
        assert 'plugin_info' in result
        
        # Vérifier plugin_info
        assert result['plugin_info']['name'] == 'caesar'
        assert result['plugin_info']['version'] == '1.0.0'
        assert 'execution_time_ms' in result['plugin_info']
        
        # Vérifier results
        assert len(result['results']) == 1
        res = result['results'][0]
        
        assert 'id' in res
        assert 'text_output' in res
        assert 'confidence' in res
        assert 'parameters' in res
        assert 'metadata' in res
        
        # Vérifier valeurs
        assert res['text_output'] == 'BCD'
        assert res['parameters']['mode'] == 'encode'
        assert res['parameters']['shift'] == 1
    
    def test_output_format_detect(self, app):
        """Vérifie le format de sortie en mode detect."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        
        if not plugins_dir.exists():
            pytest.skip(f"Répertoire plugins non trouvé: {plugins_dir}")
        
        manager = PluginManager(str(plugins_dir), app)
        manager.discover_plugins()
        
        result = manager.execute_plugin('caesar', {
            'text': 'ABCDEFGH',
            'mode': 'detect'
        })
        
        assert result['status'] == 'ok'
        assert len(result['results']) == 1
        
        # En mode detect, doit retourner des métadonnées de détection
        res = result['results'][0]
        assert 'metadata' in res
        assert 'is_match' in res['metadata']
        assert 'detection_score' in res['metadata']
