"""
Tests unitaires pour les wrappers de plugins.

Ces tests vérifient :
- L'initialisation des wrappers (Python, Binary)
- L'exécution correcte des plugins
- La gestion des erreurs
- Le nettoyage des ressources
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path

from gc_backend.plugins.wrappers import (
    PythonPluginWrapper,
    BinaryPluginWrapper,
    PluginMetadata,
    PluginType,
    create_plugin_wrapper
)


@pytest.fixture
def temp_plugin_dir():
    """Crée un répertoire temporaire pour les plugins de test."""
    temp_dir = tempfile.mkdtemp()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def basic_metadata():
    """Métadonnées de base pour un plugin de test."""
    return PluginMetadata(
        name="test_plugin",
        version="1.0.0",
        plugin_type=PluginType.PYTHON,
        entry_point="main.py",
        path="/fake/path",
        timeout_seconds=30
    )


class TestPythonPluginWrapper:
    """Tests pour le wrapper de plugins Python."""
    
    def test_create_wrapper(self, basic_metadata):
        """Test création d'un wrapper Python."""
        wrapper = PythonPluginWrapper(basic_metadata)
        
        assert wrapper.metadata.name == "test_plugin"
        assert wrapper._module is None
        assert wrapper._instance is None
    
    def test_initialize_plugin_not_found(self, basic_metadata):
        """Test initialisation avec fichier non trouvé."""
        wrapper = PythonPluginWrapper(basic_metadata)
        
        result = wrapper.initialize()
        
        assert result is False
    
    def test_initialize_valid_plugin(self, temp_plugin_dir, basic_metadata):
        """Test initialisation d'un plugin valide."""
        # Créer un plugin simple
        plugin_code = '''
class TestPlugin:
    def __init__(self):
        self.name = "test_plugin"
    
    def execute(self, inputs):
        return {
            "status": "ok",
            "summary": "Test successful",
            "results": [],
            "plugin_info": {
                "name": "test_plugin",
                "version": "1.0.0",
                "execution_time_ms": 0
            }
        }
'''
        
        plugin_file = temp_plugin_dir / 'main.py'
        plugin_file.write_text(plugin_code)
        
        # Mettre à jour les métadonnées
        basic_metadata.path = str(temp_plugin_dir)
        
        wrapper = PythonPluginWrapper(basic_metadata)
        result = wrapper.initialize()
        
        assert result is True
        assert wrapper._instance is not None
    
    def test_execute_without_initialize(self, basic_metadata):
        """Test exécution sans initialisation."""
        wrapper = PythonPluginWrapper(basic_metadata)
        
        with pytest.raises(RuntimeError, match="non initialisé"):
            wrapper.execute({})
    
    def test_execute_plugin(self, temp_plugin_dir, basic_metadata):
        """Test exécution d'un plugin."""
        # Créer un plugin qui retourne les inputs
        plugin_code = '''
class TestPlugin:
    def execute(self, inputs):
        return {
            "status": "ok",
            "summary": f"Received: {inputs.get('test_param')}",
            "results": [{
                "id": "result_1",
                "text_output": inputs.get("text", ""),
                "confidence": 1.0,
                "parameters": inputs
            }],
            "plugin_info": {
                "name": "test_plugin",
                "version": "1.0.0",
                "execution_time_ms": 5
            }
        }
'''
        
        plugin_file = temp_plugin_dir / 'main.py'
        plugin_file.write_text(plugin_code)
        
        basic_metadata.path = str(temp_plugin_dir)
        
        wrapper = PythonPluginWrapper(basic_metadata)
        wrapper.initialize()
        
        # Exécuter avec des inputs
        result = wrapper.execute({
            "test_param": "hello",
            "text": "test text"
        })
        
        assert result["status"] == "ok"
        assert "hello" in result["summary"]
        assert len(result["results"]) == 1
        assert result["results"][0]["text_output"] == "test text"
    
    def test_execute_plugin_error(self, temp_plugin_dir, basic_metadata):
        """Test gestion d'erreur lors de l'exécution."""
        # Plugin qui lève une exception
        plugin_code = '''
class TestPlugin:
    def execute(self, inputs):
        raise ValueError("Test error")
'''
        
        plugin_file = temp_plugin_dir / 'main.py'
        plugin_file.write_text(plugin_code)
        
        basic_metadata.path = str(temp_plugin_dir)
        
        wrapper = PythonPluginWrapper(basic_metadata)
        wrapper.initialize()
        
        result = wrapper.execute({})
        
        # Doit retourner une erreur au format standardisé
        assert result["status"] == "error"
        assert "Test error" in result["summary"]
        assert "error" in result
    
    def test_find_plugin_class_by_convention(self, temp_plugin_dir, basic_metadata):
        """Test recherche de classe par convention de nommage."""
        # Créer plugin avec nom conventionnel
        plugin_code = '''
class TestpluginPlugin:
    def execute(self, inputs):
        return {"status": "ok"}
'''
        
        plugin_file = temp_plugin_dir / 'main.py'
        plugin_file.write_text(plugin_code)
        
        basic_metadata.path = str(temp_plugin_dir)
        
        wrapper = PythonPluginWrapper(basic_metadata)
        result = wrapper.initialize()
        
        assert result is True
        assert wrapper._instance.__class__.__name__ == "TestpluginPlugin"
    
    def test_find_plugin_class_any_suffix(self, temp_plugin_dir, basic_metadata):
        """Test recherche de classe avec suffixe Plugin."""
        # Créer plugin avec nom quelconque finissant par Plugin
        plugin_code = '''
class MyCustomPlugin:
    def execute(self, inputs):
        return {"status": "ok"}
'''
        
        plugin_file = temp_plugin_dir / 'main.py'
        plugin_file.write_text(plugin_code)
        
        basic_metadata.path = str(temp_plugin_dir)
        
        wrapper = PythonPluginWrapper(basic_metadata)
        result = wrapper.initialize()
        
        assert result is True
        assert wrapper._instance.__class__.__name__ == "MyCustomPlugin"
    
    def test_cleanup(self, temp_plugin_dir, basic_metadata):
        """Test nettoyage des ressources."""
        plugin_code = '''
class TestPlugin:
    def execute(self, inputs):
        return {"status": "ok"}
    
    def cleanup(self):
        # Méthode de nettoyage personnalisée
        pass
'''
        
        plugin_file = temp_plugin_dir / 'main.py'
        plugin_file.write_text(plugin_code)
        
        basic_metadata.path = str(temp_plugin_dir)
        
        wrapper = PythonPluginWrapper(basic_metadata)
        wrapper.initialize()
        
        assert wrapper._instance is not None
        
        result = wrapper.cleanup()
        
        assert result is True
        assert wrapper._module is None
        assert wrapper._instance is None
    
    def test_plugin_manager_injection(self, temp_plugin_dir, basic_metadata):
        """Test injection du plugin_manager dans le plugin."""
        plugin_code = '''
class TestPlugin:
    def set_plugin_manager(self, manager):
        self.manager = manager
    
    def execute(self, inputs):
        return {"status": "ok"}
'''
        
        plugin_file = temp_plugin_dir / 'main.py'
        plugin_file.write_text(plugin_code)
        
        basic_metadata.path = str(temp_plugin_dir)
        
        mock_manager = object()  # Mock simple
        wrapper = PythonPluginWrapper(basic_metadata, mock_manager)
        wrapper.initialize()
        
        # Vérifier que le manager a été injecté
        assert hasattr(wrapper._instance, 'manager')
        assert wrapper._instance.manager is mock_manager


class TestBinaryPluginWrapper:
    """Tests pour le wrapper de plugins binaires."""
    
    def test_create_wrapper(self):
        """Test création d'un wrapper binaire."""
        metadata = PluginMetadata(
            name="binary_plugin",
            version="1.0.0",
            plugin_type=PluginType.BINARY,
            entry_point="plugin.exe",
            path="/fake/path"
        )
        
        wrapper = BinaryPluginWrapper(metadata)
        
        assert wrapper.metadata.name == "binary_plugin"
        assert wrapper.binary_path.name == "plugin.exe"
    
    def test_initialize_binary_not_found(self):
        """Test initialisation avec binaire non trouvé."""
        metadata = PluginMetadata(
            name="binary_plugin",
            version="1.0.0",
            plugin_type=PluginType.BINARY,
            entry_point="nonexistent.exe",
            path="/fake/path"
        )
        
        wrapper = BinaryPluginWrapper(metadata)
        result = wrapper.initialize()
        
        assert result is False
    
    def test_initialize_valid_binary(self, temp_plugin_dir):
        """Test initialisation d'un binaire valide."""
        # Créer un faux binaire (script Python exécutable)
        if sys.platform == 'win32':
            binary_file = temp_plugin_dir / 'plugin.exe'
        else:
            binary_file = temp_plugin_dir / 'plugin.sh'
        
        binary_file.write_text('#!/bin/sh\necho "test"')
        
        # Sur Unix, rendre exécutable
        if sys.platform != 'win32':
            import os
            os.chmod(binary_file, 0o755)
        
        metadata = PluginMetadata(
            name="binary_plugin",
            version="1.0.0",
            plugin_type=PluginType.BINARY,
            entry_point=binary_file.name,
            path=str(temp_plugin_dir)
        )
        
        wrapper = BinaryPluginWrapper(metadata)
        
        # Sur Windows, devrait passer (extension .exe)
        # Sur Unix, devrait passer (permissions OK)
        if sys.platform == 'win32':
            assert wrapper.initialize() is True
        else:
            assert wrapper.initialize() is True
    
    def test_cleanup(self):
        """Test nettoyage (rien à faire pour binaire)."""
        metadata = PluginMetadata(
            name="binary_plugin",
            version="1.0.0",
            plugin_type=PluginType.BINARY,
            entry_point="plugin.exe",
            path="/fake/path"
        )
        
        wrapper = BinaryPluginWrapper(metadata)
        result = wrapper.cleanup()
        
        assert result is True


class TestPluginWrapperFactory:
    """Tests pour la factory de wrappers."""
    
    def test_create_python_wrapper(self):
        """Test création d'un wrapper Python via factory."""
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            plugin_type=PluginType.PYTHON,
            entry_point="main.py",
            path="/fake/path"
        )
        
        wrapper = create_plugin_wrapper("python", metadata)
        
        assert isinstance(wrapper, PythonPluginWrapper)
    
    def test_create_binary_wrapper(self):
        """Test création d'un wrapper binaire via factory."""
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            plugin_type=PluginType.BINARY,
            entry_point="plugin.exe",
            path="/fake/path"
        )
        
        wrapper = create_plugin_wrapper("binary", metadata)
        
        assert isinstance(wrapper, BinaryPluginWrapper)
    
    def test_create_unsupported_type(self):
        """Test création avec type non supporté."""
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            plugin_type=PluginType.PYTHON,
            entry_point="main.py",
            path="/fake/path"
        )
        
        wrapper = create_plugin_wrapper("invalid_type", metadata)
        
        assert wrapper is None
    
    def test_create_future_type_fallback(self):
        """Test fallback vers binaire pour types futurs (rust, wasm)."""
        metadata = PluginMetadata(
            name="test",
            version="1.0.0",
            plugin_type=PluginType.RUST,
            entry_point="plugin.so",
            path="/fake/path"
        )
        
        wrapper = create_plugin_wrapper("rust", metadata)
        
        # Devrait retourner BinaryWrapper en attendant implémentation
        assert isinstance(wrapper, BinaryPluginWrapper)


# Import nécessaire pour test binaire
import sys
