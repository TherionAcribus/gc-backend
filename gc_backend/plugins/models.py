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
                metadata = json.loads(self.metadata_json) if self.metadata_json else {}
                data['metadata'] = metadata
                
                # Transformer input_types en input_schema (JSON Schema)
                if 'input_types' in metadata:
                    data['input_schema'] = self._convert_input_types_to_json_schema(metadata['input_types'])
                
                # Ajouter output_types si présent
                if 'output_types' in metadata:
                    data['output_types'] = metadata['output_types']
                    
            except json.JSONDecodeError:
                data['metadata'] = {}
        
        return data
    
    def _convert_input_types_to_json_schema(self, input_types: dict) -> dict:
        """
        Convertit le format input_types personnalisé en JSON Schema standard.
        
        Args:
            input_types (dict): Format personnalisé du plugin.json
            
        Returns:
            dict: JSON Schema standard
        """
        schema = {
            'type': 'object',
            'properties': {},
            'required': []
        }
        
        for key, field_def in input_types.items():
            prop = {}
            
            # Type de base
            field_type = field_def.get('type', 'string')
            
            # Mapping des types personnalisés vers JSON Schema
            if field_type == 'select':
                prop['type'] = 'string'
                # Options comme enum
                options = field_def.get('options', [])
                if options:
                    # Support des options simples ou avec label
                    if isinstance(options[0], dict):
                        prop['enum'] = [opt['value'] for opt in options]
                    else:
                        prop['enum'] = options
            elif field_type == 'checkbox':
                prop['type'] = 'boolean'
            elif field_type == 'number':
                prop['type'] = 'number'
                if 'min' in field_def:
                    prop['minimum'] = field_def['min']
                if 'max' in field_def:
                    prop['maximum'] = field_def['max']
                if 'step' in field_def:
                    prop['multipleOf'] = field_def['step']
            elif field_type == 'integer':
                prop['type'] = 'integer'
                if 'min' in field_def:
                    prop['minimum'] = field_def['min']
                if 'max' in field_def:
                    prop['maximum'] = field_def['max']
            else:  # string par défaut
                prop['type'] = 'string'
            
            # Métadonnées
            if 'label' in field_def:
                prop['title'] = field_def['label']
            if 'description' in field_def:
                prop['description'] = field_def['description']
            if 'default' in field_def:
                prop['default'] = field_def['default']
            if 'placeholder' in field_def:
                prop['placeholder'] = field_def['placeholder']
            
            schema['properties'][key] = prop
            
            # Champs requis (par défaut, tous sauf 'text' sont optionnels)
            if field_def.get('required', False):
                schema['required'].append(key)
        
        return schema
