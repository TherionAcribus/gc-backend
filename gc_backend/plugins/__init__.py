"""
Module de gestion des plugins pour MysterAI.

Ce module fournit l'infrastructure complète pour :
- Découvrir et charger dynamiquement les plugins
- Valider leur conformité au contrat API
- Exécuter les plugins de manière synchrone ou asynchrone
- Gérer les wrappers pour différents types de plugins (Python, Rust, Binary, etc.)

Structure :
    - models.py : Modèles SQLAlchemy pour la persistance
    - plugin_manager.py : Gestionnaire principal de plugins
    - wrappers.py : Wrappers d'exécution par type de plugin
    - schemas/ : Schémas JSON de validation

Utilisation basique :
    from gc_backend.plugins import PluginManager, Plugin
    
    # Créer le gestionnaire
    manager = PluginManager('plugins/', app)
    
    # Découvrir les plugins
    manager.discover_plugins()
    
    # Exécuter un plugin
    result = manager.execute_plugin('caesar', {
        'text': 'HELLO',
        'mode': 'encode',
        'shift': 13
    })
"""

from .models import Plugin
from .plugin_manager import PluginManager
from .wrappers import (
    PluginInterface,
    PythonPluginWrapper,
    BinaryPluginWrapper,
    PluginMetadata,
    PluginType,
    create_plugin_wrapper
)

__all__ = [
    # Modèle
    'Plugin',
    
    # Gestionnaire
    'PluginManager',
    
    # Wrappers
    'PluginInterface',
    'PythonPluginWrapper',
    'BinaryPluginWrapper',
    'PluginMetadata',
    'PluginType',
    'create_plugin_wrapper',
]
