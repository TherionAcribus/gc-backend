"""
Modèles de base de données pour les plugins.

Ce module définit le modèle Plugin qui stocke les métadonnées
et informations de configuration de chaque plugin découvert.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON
from sqlalchemy.sql import func

from ..database import db


class Plugin(db.Model):
    """
    Modèle représentant un plugin tel que défini par son plugin.json.
    
    Un plugin peut être :
    - Official : fourni avec l'application (lecture seule)
    - Custom : ajouté par l'utilisateur
    
    Attributes:
        id (int): Identifiant unique auto-incrémenté
        name (str): Nom unique du plugin (ex: "caesar", "bacon_code")
        version (str): Version sémantique du plugin (ex: "1.0.0")
        plugin_api_version (str): Version de l'API du plugin (ex: "2.0")
        description (str): Description courte du plugin
        author (str): Auteur du plugin
        plugin_type (str): Type de plugin ("python", "rust", "binary", "wasm")
        source (str): Source du plugin ("official", "custom")
        path (str): Chemin absolu vers le dossier du plugin
        entry_point (str): Point d'entrée (ex: "main.py", "plugin.wasm")
        categories (list): Liste des catégories (ex: ["Substitution", "Caesar"])
        input_types (dict): Définition des types d'entrée du formulaire
        heavy_cpu (bool): Indique si le plugin est CPU intensif
        needs_network (bool): Indique si le plugin nécessite un accès réseau
        needs_filesystem (bool): Indique si le plugin nécessite accès au système de fichiers
        enabled (bool): Statut d'activation du plugin
        metadata_json (str): plugin.json complet en chaîne JSON
        created_at (datetime): Date de création de l'entrée
        updated_at (datetime): Date de dernière mise à jour
    """
    
    __tablename__ = 'plugins'
    
    # Clé primaire
    id = Column(Integer, primary_key=True)
    
    # Identification du plugin
    name = Column(String(128), unique=True, nullable=False, index=True)
    version = Column(String(32), nullable=False)
    plugin_api_version = Column(String(16), default="2.0")
    
    # Métadonnées descriptives
    description = Column(Text)
    author = Column(String(128))
    
    # Type et source
    plugin_type = Column(String(32), nullable=False)  # python, rust, binary, wasm
    source = Column(String(16), nullable=False, index=True)  # official, custom
    
    # Localisation
    path = Column(String(512), nullable=False)
    entry_point = Column(String(256))
    
    # Configuration
    categories = Column(JSON, default=list)  # ["Substitution", "Transposition"]
    input_types = Column(JSON, default=dict)  # Configuration des inputs du formulaire
    
    # Policies d'exécution
    heavy_cpu = Column(Boolean, default=False)  # Nécessite ProcessPool
    needs_network = Column(Boolean, default=False)  # Nécessite accès réseau
    needs_filesystem = Column(Boolean, default=False)  # Nécessite accès FS
    
    # État
    enabled = Column(Boolean, default=True, index=True)
    
    # Métadonnées complètes (plugin.json entier)
    metadata_json = Column(Text)
    
    # Timestamps
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f'<Plugin {self.name} v{self.version} ({self.source})>'
    
    def to_dict(self, include_metadata=False):
        """
        Convertit le plugin en dictionnaire pour l'API.
        
        Args:
            include_metadata (bool): Inclure le metadata_json complet
            
        Returns:
            dict: Représentation du plugin
        """
        data = {
            'id': self.id,
            'name': self.name,
            'version': self.version,
            'plugin_api_version': self.plugin_api_version,
            'description': self.description,
            'author': self.author,
            'plugin_type': self.plugin_type,
            'source': self.source,
            'categories': self.categories or [],
            'heavy_cpu': self.heavy_cpu,
            'needs_network': self.needs_network,
            'needs_filesystem': self.needs_filesystem,
            'enabled': self.enabled,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        
        if include_metadata:
            import json
            try:
                data['metadata'] = json.loads(self.metadata_json) if self.metadata_json else {}
            except json.JSONDecodeError:
                data['metadata'] = {}
        
        return data
