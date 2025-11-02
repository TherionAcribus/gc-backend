"""
Tests unitaires pour le PluginManager.

Ces tests vérifient :
- La découverte de plugins dans official/ et custom/
- La validation contre le schéma JSON
- L'enregistrement en base de données
- Le nettoyage des plugins supprimés
- Le filtrage et la liste des plugins
"""

import pytest
import json
import tempfile
import shutil
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


@pytest.fixture
def temp_plugins_dir():
    """Crée un répertoire temporaire pour les plugins de test."""
    temp_dir = tempfile.mkdtemp()
    plugins_dir = Path(temp_dir)
    
    # Créer structure official/ et custom/
    (plugins_dir / 'official').mkdir(parents=True, exist_ok=True)
    (plugins_dir / 'custom').mkdir(parents=True, exist_ok=True)
    
    yield plugins_dir
    
    # Nettoyage
    shutil.rmtree(temp_dir)


@pytest.fixture
def valid_plugin_json():
    """Retourne un plugin.json valide pour les tests."""
    return {
        "name": "test_plugin",
        "version": "1.0.0",
        "plugin_api_version": "2.0",
        "description": "Plugin de test",
        "author": "Test Author",
        "plugin_type": "python",
        "entry_point": "main.py",
        "categories": ["Test"],
        "input_types": {
            "text": {
                "type": "string",
                "label": "Texte à traiter"
            }
        }
    }


def create_test_plugin(
    plugins_dir: Path,
    source: str,
    plugin_name: str,
    plugin_data: dict
) -> Path:
    """
    Crée un plugin de test dans le répertoire spécifié.
    
    Args:
        plugins_dir: Répertoire racine des plugins
        source: 'official' ou 'custom'
        plugin_name: Nom du plugin
        plugin_data: Données du plugin.json
        
    Returns:
        Path: Chemin vers le dossier du plugin créé
    """
    plugin_dir = plugins_dir / source / plugin_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    
    # Écrire plugin.json
    with open(plugin_dir / 'plugin.json', 'w', encoding='utf-8') as f:
        json.dump(plugin_data, f, indent=2)
    
    # Créer un main.py vide
    (plugin_dir / 'main.py').write_text('# Plugin de test\n')
    
    return plugin_dir


class TestPluginManagerDiscovery:
    """Tests de découverte de plugins."""
    
    def test_discover_empty_directories(self, app, temp_plugins_dir):
        """Test découverte avec répertoires vides."""
        manager = PluginManager(str(temp_plugins_dir), app)
        
        discovered = manager.discover_plugins()
        
        assert len(discovered) == 0
    
    def test_discover_single_official_plugin(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test découverte d'un plugin official."""
        # Créer un plugin de test
        create_test_plugin(
            temp_plugins_dir, 'official', 'test_plugin', valid_plugin_json
        )
        
        manager = PluginManager(str(temp_plugins_dir), app)
        discovered = manager.discover_plugins()
        
        assert len(discovered) == 1
        assert discovered[0]['name'] == 'test_plugin'
        assert discovered[0]['source'] == 'official'
        
        # Vérifier en DB
        with app.app_context():
            plugin = Plugin.query.filter_by(name='test_plugin').first()
            assert plugin is not None
            assert plugin.source == 'official'
            assert plugin.version == '1.0.0'
    
    def test_discover_multiple_plugins(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test découverte de plusieurs plugins dans different sources."""
        # Plugin official
        official_data = valid_plugin_json.copy()
        official_data['name'] = 'official_plugin'
        create_test_plugin(
            temp_plugins_dir, 'official', 'official_plugin', official_data
        )
        
        # Plugin custom
        custom_data = valid_plugin_json.copy()
        custom_data['name'] = 'custom_plugin'
        create_test_plugin(
            temp_plugins_dir, 'custom', 'custom_plugin', custom_data
        )
        
        manager = PluginManager(str(temp_plugins_dir), app)
        discovered = manager.discover_plugins()
        
        assert len(discovered) == 2
        
        plugin_names = {p['name'] for p in discovered}
        assert 'official_plugin' in plugin_names
        assert 'custom_plugin' in plugin_names
        
        # Vérifier sources
        sources = {p['name']: p['source'] for p in discovered}
        assert sources['official_plugin'] == 'official'
        assert sources['custom_plugin'] == 'custom'
    
    def test_discover_invalid_json_syntax(
        self, app, temp_plugins_dir
    ):
        """Test avec un JSON invalide syntaxiquement."""
        plugin_dir = temp_plugins_dir / 'official' / 'bad_plugin'
        plugin_dir.mkdir(parents=True)
        
        # Écrire un JSON invalide
        (plugin_dir / 'plugin.json').write_text('{invalid json}')
        
        manager = PluginManager(str(temp_plugins_dir), app)
        discovered = manager.discover_plugins()
        
        # Le plugin ne doit pas être découvert
        assert len(discovered) == 0
        
        # Mais une erreur doit être enregistrée
        errors = manager.get_discovery_errors()
        assert len(errors) > 0
    
    def test_discover_invalid_schema(
        self, app, temp_plugins_dir
    ):
        """Test avec un plugin.json valide en JSON mais invalide selon le schéma."""
        invalid_data = {
            "name": "invalid_plugin",
            # Manque version, plugin_type, entry_point
        }
        
        create_test_plugin(
            temp_plugins_dir, 'official', 'invalid_plugin', invalid_data
        )
        
        manager = PluginManager(str(temp_plugins_dir), app)
        discovered = manager.discover_plugins()
        
        # Le plugin ne doit pas être découvert
        assert len(discovered) == 0


class TestPluginManagerValidation:
    """Tests de validation de plugins."""
    
    def test_validate_valid_plugin(self, app, valid_plugin_json):
        """Test validation d'un plugin valide."""
        manager = PluginManager('dummy_path')
        
        is_valid = manager._validate_plugin_json(valid_plugin_json)
        
        assert is_valid is True
    
    def test_validate_missing_required_field(self, app, valid_plugin_json):
        """Test validation avec champ requis manquant."""
        invalid_data = valid_plugin_json.copy()
        del invalid_data['name']  # Champ requis
        
        manager = PluginManager('dummy_path')
        is_valid = manager._validate_plugin_json(invalid_data)
        
        assert is_valid is False
    
    def test_validate_invalid_plugin_type(self, app, valid_plugin_json):
        """Test validation avec type de plugin invalide."""
        invalid_data = valid_plugin_json.copy()
        invalid_data['plugin_type'] = 'invalid_type'
        
        manager = PluginManager('dummy_path')
        is_valid = manager._validate_plugin_json(invalid_data)
        
        assert is_valid is False
    
    def test_validate_invalid_name_format(self, app, valid_plugin_json):
        """Test validation avec format de nom invalide (pas snake_case)."""
        invalid_data = valid_plugin_json.copy()
        invalid_data['name'] = 'Invalid-Name-With-Dashes'
        
        manager = PluginManager('dummy_path')
        is_valid = manager._validate_plugin_json(invalid_data)
        
        assert is_valid is False


class TestPluginManagerUpdate:
    """Tests de mise à jour de plugins."""
    
    def test_update_plugin_version(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test mise à jour de la version d'un plugin."""
        # Créer plugin v1.0.0
        create_test_plugin(
            temp_plugins_dir, 'official', 'test_plugin', valid_plugin_json
        )
        
        manager = PluginManager(str(temp_plugins_dir), app)
        manager.discover_plugins()
        
        with app.app_context():
            plugin = Plugin.query.filter_by(name='test_plugin').first()
            assert plugin.version == '1.0.0'
        
        # Modifier version
        updated_data = valid_plugin_json.copy()
        updated_data['version'] = '2.0.0'
        
        # Recréer le plugin avec nouvelle version
        plugin_dir = temp_plugins_dir / 'official' / 'test_plugin'
        with open(plugin_dir / 'plugin.json', 'w') as f:
            json.dump(updated_data, f)
        
        # Redécouvrir
        manager.discover_plugins()
        
        with app.app_context():
            plugin = Plugin.query.filter_by(name='test_plugin').first()
            assert plugin.version == '2.0.0'


class TestPluginManagerCleanup:
    """Tests de nettoyage de plugins supprimés."""
    
    def test_cleanup_deleted_plugin(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test suppression en DB d'un plugin supprimé physiquement."""
        # Créer et découvrir un plugin
        plugin_dir = create_test_plugin(
            temp_plugins_dir, 'official', 'test_plugin', valid_plugin_json
        )
        
        manager = PluginManager(str(temp_plugins_dir), app)
        manager.discover_plugins()
        
        with app.app_context():
            assert Plugin.query.count() == 1
        
        # Supprimer physiquement le plugin
        shutil.rmtree(plugin_dir)
        
        # Redécouvrir
        manager.discover_plugins()
        
        with app.app_context():
            # Le plugin doit être supprimé de la DB
            assert Plugin.query.count() == 0


class TestPluginManagerListing:
    """Tests de listage et filtrage de plugins."""
    
    def test_list_all_plugins(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test listage de tous les plugins."""
        # Créer 2 plugins
        data1 = valid_plugin_json.copy()
        data1['name'] = 'plugin1'
        create_test_plugin(temp_plugins_dir, 'official', 'plugin1', data1)
        
        data2 = valid_plugin_json.copy()
        data2['name'] = 'plugin2'
        create_test_plugin(temp_plugins_dir, 'custom', 'plugin2', data2)
        
        manager = PluginManager(str(temp_plugins_dir), app)
        manager.discover_plugins()
        
        plugins = manager.list_plugins()
        
        assert len(plugins) == 2
    
    def test_list_plugins_by_source(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test filtrage par source."""
        # Créer plugins dans différentes sources
        data1 = valid_plugin_json.copy()
        data1['name'] = 'official1'
        create_test_plugin(temp_plugins_dir, 'official', 'official1', data1)
        
        data2 = valid_plugin_json.copy()
        data2['name'] = 'custom1'
        create_test_plugin(temp_plugins_dir, 'custom', 'custom1', data2)
        
        manager = PluginManager(str(temp_plugins_dir), app)
        manager.discover_plugins()
        
        # Filtrer official
        official_plugins = manager.list_plugins(source='official')
        assert len(official_plugins) == 1
        assert official_plugins[0]['name'] == 'official1'
        
        # Filtrer custom
        custom_plugins = manager.list_plugins(source='custom')
        assert len(custom_plugins) == 1
        assert custom_plugins[0]['name'] == 'custom1'
    
    def test_list_plugins_by_category(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test filtrage par catégorie."""
        # Plugin avec catégorie "Test"
        data1 = valid_plugin_json.copy()
        data1['name'] = 'test_cat'
        data1['categories'] = ['Test']
        create_test_plugin(temp_plugins_dir, 'official', 'test_cat', data1)
        
        # Plugin avec catégorie "Other"
        data2 = valid_plugin_json.copy()
        data2['name'] = 'other_cat'
        data2['categories'] = ['Other']
        create_test_plugin(temp_plugins_dir, 'official', 'other_cat', data2)
        
        manager = PluginManager(str(temp_plugins_dir), app)
        manager.discover_plugins()
        
        # Filtrer par catégorie
        test_plugins = manager.list_plugins(category='Test')
        assert len(test_plugins) == 1
        assert test_plugins[0]['name'] == 'test_cat'
    
    def test_list_enabled_only(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test filtrage par statut enabled."""
        # Créer plugin
        create_test_plugin(
            temp_plugins_dir, 'official', 'test_plugin', valid_plugin_json
        )
        
        manager = PluginManager(str(temp_plugins_dir), app)
        manager.discover_plugins()
        
        # Désactiver le plugin
        with app.app_context():
            plugin = Plugin.query.filter_by(name='test_plugin').first()
            plugin.enabled = False
            db.session.commit()
        
        # Lister avec enabled_only=True
        enabled_plugins = manager.list_plugins(enabled_only=True)
        assert len(enabled_plugins) == 0
        
        # Lister avec enabled_only=False
        all_plugins = manager.list_plugins(enabled_only=False)
        assert len(all_plugins) == 1


class TestPluginManagerUtilities:
    """Tests des méthodes utilitaires."""
    
    def test_get_plugin_info(
        self, app, temp_plugins_dir, valid_plugin_json
    ):
        """Test récupération des informations d'un plugin."""
        create_test_plugin(
            temp_plugins_dir, 'official', 'test_plugin', valid_plugin_json
        )
        
        manager = PluginManager(str(temp_plugins_dir), app)
        manager.discover_plugins()
        
        info = manager.get_plugin_info('test_plugin')
        
        assert info is not None
        assert info['name'] == 'test_plugin'
        assert info['version'] == '1.0.0'
        assert 'metadata' in info  # include_metadata=True par défaut
    
    def test_get_plugin_info_not_found(self, app, temp_plugins_dir):
        """Test récupération d'un plugin inexistant."""
        manager = PluginManager(str(temp_plugins_dir), app)
        
        info = manager.get_plugin_info('nonexistent')
        
        assert info is None
    
    def test_clear_errors(self, app):
        """Test nettoyage des erreurs."""
        manager = PluginManager('dummy_path')
        manager._loading_errors['path1'] = 'error1'
        manager._loading_errors['path2'] = 'error2'
        
        assert len(manager.get_discovery_errors()) == 2
        
        manager.clear_errors()
        
        assert len(manager.get_discovery_errors()) == 0
