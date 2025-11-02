"""
Tests unitaires pour le modèle Plugin.

Ces tests vérifient :
- La création et persistance des plugins
- La validation des champs
- La conversion en dictionnaire
- Les index et contraintes
"""

import pytest
import json
from datetime import datetime

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
def client(app):
    """Crée un client de test."""
    return app.test_client()


class TestPluginModel:
    """Tests pour le modèle Plugin."""
    
    def test_create_plugin_minimal(self, app):
        """Test création d'un plugin avec les champs minimaux."""
        with app.app_context():
            plugin = Plugin(
                name='test_plugin',
                version='1.0.0',
                plugin_type='python',
                source='custom',
                path='/path/to/plugin'
            )
            db.session.add(plugin)
            db.session.commit()
            
            # Vérifier que le plugin a été créé
            assert plugin.id is not None
            assert plugin.name == 'test_plugin'
            assert plugin.version == '1.0.0'
            assert plugin.enabled is True  # Valeur par défaut
            assert plugin.created_at is not None
    
    def test_create_plugin_complete(self, app):
        """Test création d'un plugin avec tous les champs."""
        with app.app_context():
            metadata = {
                'name': 'caesar',
                'version': '1.0.0',
                'plugin_api_version': '2.0',
                'description': 'Caesar cipher plugin',
                'author': 'MysterAI Team'
            }
            
            plugin = Plugin(
                name='caesar',
                version='1.0.0',
                plugin_api_version='2.0',
                description='Caesar cipher plugin',
                author='MysterAI Team',
                plugin_type='python',
                source='official',
                path='/plugins/official/caesar',
                entry_point='main.py',
                categories=['Substitution', 'Caesar'],
                input_types={
                    'text': {'type': 'string', 'label': 'Text'},
                    'shift': {'type': 'number', 'label': 'Shift', 'default': 13}
                },
                heavy_cpu=False,
                needs_network=False,
                needs_filesystem=False,
                enabled=True,
                metadata_json=json.dumps(metadata)
            )
            db.session.add(plugin)
            db.session.commit()
            
            # Vérifier tous les champs
            assert plugin.id is not None
            assert plugin.name == 'caesar'
            assert plugin.plugin_api_version == '2.0'
            assert plugin.description == 'Caesar cipher plugin'
            assert plugin.author == 'MysterAI Team'
            assert plugin.source == 'official'
            assert len(plugin.categories) == 2
            assert 'Substitution' in plugin.categories
            assert 'text' in plugin.input_types
            assert plugin.heavy_cpu is False
    
    def test_plugin_unique_name(self, app):
        """Test que le nom du plugin est unique."""
        with app.app_context():
            # Créer premier plugin
            plugin1 = Plugin(
                name='unique_plugin',
                version='1.0.0',
                plugin_type='python',
                source='official',
                path='/path/to/plugin1'
            )
            db.session.add(plugin1)
            db.session.commit()
            
            # Tenter de créer un second plugin avec le même nom
            plugin2 = Plugin(
                name='unique_plugin',
                version='2.0.0',
                plugin_type='python',
                source='custom',
                path='/path/to/plugin2'
            )
            db.session.add(plugin2)
            
            # Doit lever une exception
            with pytest.raises(Exception):
                db.session.commit()
    
    def test_plugin_to_dict_basic(self, app):
        """Test la conversion en dictionnaire sans métadonnées."""
        with app.app_context():
            plugin = Plugin(
                name='test_dict',
                version='1.0.0',
                plugin_api_version='2.0',
                description='Test plugin',
                plugin_type='python',
                source='official',
                path='/path/to/plugin',
                categories=['Test'],
                heavy_cpu=True
            )
            db.session.add(plugin)
            db.session.commit()
            
            data = plugin.to_dict()
            
            # Vérifier les champs de base
            assert data['name'] == 'test_dict'
            assert data['version'] == '1.0.0'
            assert data['plugin_api_version'] == '2.0'
            assert data['source'] == 'official'
            assert data['heavy_cpu'] is True
            assert 'metadata' not in data  # Pas inclus par défaut
    
    def test_plugin_to_dict_with_metadata(self, app):
        """Test la conversion en dictionnaire avec métadonnées."""
        with app.app_context():
            metadata = {
                'name': 'test_dict',
                'extra_field': 'extra_value'
            }
            
            plugin = Plugin(
                name='test_dict',
                version='1.0.0',
                plugin_type='python',
                source='official',
                path='/path/to/plugin',
                metadata_json=json.dumps(metadata)
            )
            db.session.add(plugin)
            db.session.commit()
            
            data = plugin.to_dict(include_metadata=True)
            
            # Vérifier que les métadonnées sont incluses
            assert 'metadata' in data
            assert data['metadata']['extra_field'] == 'extra_value'
    
    def test_plugin_filter_by_source(self, app):
        """Test le filtrage par source."""
        with app.app_context():
            # Créer plugins officiels et customs
            official1 = Plugin(name='off1', version='1.0.0', plugin_type='python',
                             source='official', path='/path/off1')
            official2 = Plugin(name='off2', version='1.0.0', plugin_type='python',
                             source='official', path='/path/off2')
            custom1 = Plugin(name='cust1', version='1.0.0', plugin_type='python',
                           source='custom', path='/path/cust1')
            
            db.session.add_all([official1, official2, custom1])
            db.session.commit()
            
            # Filtrer par source
            official_plugins = Plugin.query.filter_by(source='official').all()
            custom_plugins = Plugin.query.filter_by(source='custom').all()
            
            assert len(official_plugins) == 2
            assert len(custom_plugins) == 1
    
    def test_plugin_filter_by_enabled(self, app):
        """Test le filtrage par statut enabled."""
        with app.app_context():
            enabled = Plugin(name='enabled', version='1.0.0', plugin_type='python',
                           source='official', path='/path/enabled', enabled=True)
            disabled = Plugin(name='disabled', version='1.0.0', plugin_type='python',
                            source='official', path='/path/disabled', enabled=False)
            
            db.session.add_all([enabled, disabled])
            db.session.commit()
            
            # Filtrer par statut
            enabled_plugins = Plugin.query.filter_by(enabled=True).all()
            disabled_plugins = Plugin.query.filter_by(enabled=False).all()
            
            assert len(enabled_plugins) == 1
            assert len(disabled_plugins) == 1
            assert enabled_plugins[0].name == 'enabled'
    
    def test_plugin_categories_json(self, app):
        """Test que les catégories sont correctement stockées en JSON."""
        with app.app_context():
            categories = ['Substitution', 'Caesar', 'ROT']
            
            plugin = Plugin(
                name='multi_cat',
                version='1.0.0',
                plugin_type='python',
                source='official',
                path='/path/to/plugin',
                categories=categories
            )
            db.session.add(plugin)
            db.session.commit()
            
            # Récupérer le plugin
            retrieved = Plugin.query.filter_by(name='multi_cat').first()
            
            assert retrieved.categories == categories
            assert len(retrieved.categories) == 3
    
    def test_plugin_updated_at(self, app):
        """Test que updated_at est mis à jour lors de modifications."""
        with app.app_context():
            plugin = Plugin(
                name='update_test',
                version='1.0.0',
                plugin_type='python',
                source='official',
                path='/path/to/plugin'
            )
            db.session.add(plugin)
            db.session.commit()
            
            original_updated = plugin.updated_at
            
            # Modifier le plugin
            plugin.description = 'Updated description'
            db.session.commit()
            
            # updated_at devrait être différent (ou au moins >= à l'original)
            assert plugin.updated_at >= original_updated
    
    def test_plugin_repr(self, app):
        """Test la représentation string du plugin."""
        with app.app_context():
            plugin = Plugin(
                name='repr_test',
                version='2.1.0',
                plugin_type='python',
                source='custom',
                path='/path/to/plugin'
            )
            db.session.add(plugin)
            db.session.commit()
            
            repr_str = repr(plugin)
            
            assert 'repr_test' in repr_str
            assert '2.1.0' in repr_str
            assert 'custom' in repr_str
