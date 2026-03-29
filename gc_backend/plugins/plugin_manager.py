"""
Gestionnaire de plugins pour MysterAI.

Le PluginManager est responsable de :
- Découvrir les plugins dans plugins/official/ et plugins/custom/
- Valider leur conformité au schéma JSON
- Enregistrer/mettre à jour les métadonnées en base de données
- Charger les plugins de manière lazy (on-demand)
- Gérer le cache des plugins chargés
- Exécuter les plugins et normaliser leurs sorties
"""

import os
import json
import hashlib
from typing import Dict, List, Optional, Any
from pathlib import Path
from datetime import datetime

from loguru import logger
import jsonschema

from ..database import db
from .models import Plugin
from .wrappers import (
    create_plugin_wrapper,
    PluginMetadata,
    PluginType,
    PluginInterface
)
from ..utils.preferences import get_value_or_default


class PluginManager:
    """
    Gestionnaire central des plugins.
    
    Attributes:
        plugins_dir (str): Répertoire racine des plugins
        app: Instance de l'application Flask
        loaded_plugins (dict): Cache des plugins chargés en mémoire
        _schema (dict): Schéma JSON de validation
        _plugin_cache (dict): Cache des métadonnées des plugins
        _loading_errors (dict): Erreurs de chargement par plugin
    """
    
    def __init__(self, plugins_dir: str, app=None):
        """
        Initialise le gestionnaire de plugins.
        
        Args:
            plugins_dir (str): Chemin vers le répertoire racine des plugins
            app: Instance de l'application Flask (optionnel)
        """
        self.plugins_dir = Path(plugins_dir)
        self.app = app
        self.loaded_plugins: Dict[str, Any] = {}
        self._schema: Optional[Dict] = None
        self._plugin_cache: Dict[str, Dict] = {}
        self._loading_errors: Dict[str, str] = {}
        self.lazy_mode: bool = True
        self.default_timeout: int = 60
        self.allow_long_running: bool = False
        
        # Charger le schéma de validation
        self._load_schema()
        self._load_runtime_preferences()
        
        logger.info(f"PluginManager initialisé avec répertoire: {self.plugins_dir}")
    
    # =========================================================================
    # Découverte et validation des plugins
    # =========================================================================
    
    def discover_plugins(self) -> List[Dict]:
        """
        Scanne les répertoires plugins/official/ et plugins/custom/ pour
        découvrir tous les fichiers plugin.json.
        
        - Valide chaque plugin.json contre le schéma
        - Met à jour la base de données (upsert)
        - Supprime les plugins qui n'existent plus physiquement
        - Calcule un hash pour détecter les changements
        
        Returns:
            List[Dict]: Liste des informations des plugins découverts
        """
        # Recharger le schéma pour prendre en compte les modifications récentes
        self._load_schema()
        
        discovered_plugins = []
        discovered_paths = set()
        
        # Scanner official et custom
        sources = {
            'official': self.plugins_dir / 'official',
            'custom': self.plugins_dir / 'custom'
        }
        
        for source_name, source_path in sources.items():
            if not source_path.exists():
                logger.warning(f"Répertoire {source_name} non trouvé: {source_path}")
                continue
            
            logger.debug(f"Scan des plugins {source_name} dans: {source_path}")
            
            # Parcourir récursivement pour trouver plugin.json
            for plugin_json_path in source_path.rglob('plugin.json'):
                plugin_dir = plugin_json_path.parent
                discovered_paths.add(str(plugin_dir))
                
                logger.debug(f"Trouvé plugin.json: {plugin_json_path}")
                
                try:
                    # Charger et valider le plugin
                    plugin_info = self._load_and_validate_plugin(
                        plugin_json_path,
                        source_name
                    )
                    
                    if plugin_info:
                        plugin_info['path'] = str(plugin_dir)
                        plugin_info['source'] = source_name
                        
                        # Mettre à jour en base de données
                        self._update_plugin_in_db(plugin_info)
                        
                        discovered_plugins.append(plugin_info)
                        
                        logger.info(
                            f"Plugin découvert: {plugin_info['name']} v{plugin_info['version']} "
                            f"({source_name})"
                        )
                    
                except Exception as e:
                    logger.opt(exception=e).error(
                        "Erreur lors du chargement de {}: {}",
                        plugin_json_path,
                        e,
                    )
                    self._loading_errors[str(plugin_dir)] = str(e)
        
        # Nettoyer les plugins qui n'existent plus
        if self.app:
            with self.app.app_context():
                self._cleanup_deleted_plugins(discovered_paths)
        
        logger.info(
            f"Découverte terminée: {len(discovered_plugins)} plugins trouvés "
            f"({len(self._loading_errors)} erreurs)"
        )
        
        return discovered_plugins
    
    def _load_and_validate_plugin(
        self,
        plugin_json_path: Path,
        source: str
    ) -> Optional[Dict]:
        """
        Charge un fichier plugin.json et le valide contre le schéma.
        
        Args:
            plugin_json_path (Path): Chemin vers plugin.json
            source (str): Source du plugin ('official' ou 'custom')
            
        Returns:
            Optional[Dict]: Informations du plugin si valide, None sinon
        """
        try:
            # Charger le JSON
            with open(plugin_json_path, 'r', encoding='utf-8') as f:
                plugin_data = json.load(f)
            
            # Valider contre le schéma
            if not self._validate_plugin_json(plugin_data):
                error_msg = f"Validation échouée pour {plugin_json_path}"
                logger.error(error_msg)
                # Enregistrer l'erreur
                plugin_dir = str(plugin_json_path.parent)
                self._loading_errors[plugin_dir] = error_msg
                return None
            
            # Calculer un hash pour détecter les changements
            plugin_hash = self._calculate_plugin_hash(plugin_json_path)
            plugin_data['_hash'] = plugin_hash
            
            return plugin_data
            
        except json.JSONDecodeError as e:
            error_msg = f"JSON invalide dans {plugin_json_path}: {e}"
            logger.error(error_msg)
            # Enregistrer l'erreur pour que les tests puissent la vérifier
            plugin_dir = str(plugin_json_path.parent)
            self._loading_errors[plugin_dir] = error_msg
            return None
        except Exception as e:
            error_msg = f"Erreur lecture {plugin_json_path}: {e}"
            logger.error(error_msg)
            # Enregistrer l'erreur
            plugin_dir = str(plugin_json_path.parent)
            self._loading_errors[plugin_dir] = error_msg
            return None
    
    def _validate_plugin_json(self, plugin_data: Dict) -> bool:
        """
        Valide un plugin.json contre le schéma JSON Schema.
        
        Args:
            plugin_data (Dict): Données du plugin à valider
            
        Returns:
            bool: True si valide, False sinon
        """
        if not self._schema:
            logger.warning("Schéma de validation non chargé, validation ignorée")
            return True
        
        try:
            jsonschema.validate(instance=plugin_data, schema=self._schema)
            logger.debug(f"Plugin {plugin_data.get('name')} validé avec succès")
            return True
            
        except jsonschema.ValidationError as e:
            logger.error(
                f"Erreur de validation pour plugin {plugin_data.get('name', 'unknown')}: "
                f"{e.message}"
            )
            logger.debug(f"Chemin de l'erreur: {' -> '.join(str(p) for p in e.path)}")
            return False
        except Exception as e:
            logger.error(f"Erreur lors de la validation: {e}")
            return False
    
    def _calculate_plugin_hash(self, plugin_json_path: Path) -> str:
        """
        Calcule un hash MD5 du fichier plugin.json pour détecter les modifications.
        
        Args:
            plugin_json_path (Path): Chemin vers plugin.json
            
        Returns:
            str: Hash MD5 du fichier
        """
        try:
            with open(plugin_json_path, 'rb') as f:
                file_hash = hashlib.md5(f.read()).hexdigest()
            return file_hash
        except Exception as e:
            logger.error(f"Erreur calcul hash pour {plugin_json_path}: {e}")
            return ""
    
    def _update_plugin_in_db(self, plugin_info: Dict) -> None:
        """
        Met à jour ou crée un plugin dans la base de données.
        
        Stratégie :
        - Si le plugin existe (même nom) : mise à jour
        - Si le plugin n'existe pas : création
        - Comparaison par hash pour éviter les updates inutiles
        
        Args:
            plugin_info (Dict): Informations du plugin
        """
        if not self.app:
            logger.warning("Pas d'app Flask, impossible de mettre à jour la DB")
            return
        
        try:
            with self.app.app_context():
                # Chercher le plugin existant
                existing = Plugin.query.filter_by(name=plugin_info['name']).first()
                
                if existing:
                    # Vérifier si mise à jour nécessaire (hash différent)
                    current_hash = plugin_info.get('_hash', '')
                    
                    # On stocke le hash dans metadata_json pour comparaison
                    existing_metadata = {}
                    if existing.metadata_json:
                        try:
                            existing_metadata = json.loads(existing.metadata_json)
                        except json.JSONDecodeError:
                            pass
                    
                    existing_hash = existing_metadata.get('_hash', '')
                    
                    if current_hash and current_hash == existing_hash:
                        logger.debug(
                            f"Plugin {plugin_info['name']} inchangé (hash identique)"
                        )
                        return
                    
                    # Mise à jour
                    logger.info(
                        f"Mise à jour plugin {plugin_info['name']} "
                        f"v{existing.version} -> v{plugin_info['version']}"
                    )
                    
                    self._update_plugin_fields(existing, plugin_info)
                    
                else:
                    # Création
                    logger.info(f"Création nouveau plugin: {plugin_info['name']}")
                    
                    new_plugin = Plugin(
                        name=plugin_info['name'],
                        version=plugin_info['version'],
                        plugin_api_version=plugin_info.get('plugin_api_version', '2.0'),
                        description=plugin_info.get('description', ''),
                        author=plugin_info.get('author', ''),
                        plugin_type=plugin_info['plugin_type'],
                        source=plugin_info['source'],
                        path=plugin_info['path'],
                        entry_point=plugin_info['entry_point'],
                        categories=plugin_info.get('categories', []),
                        input_types=plugin_info.get('input_types', {}),
                        heavy_cpu=plugin_info.get('heavy_cpu', False),
                        needs_network=plugin_info.get('needs_network', False),
                        needs_filesystem=plugin_info.get('needs_filesystem', False),
                        enabled=True,
                        metadata_json=json.dumps(plugin_info)
                    )
                    
                    db.session.add(new_plugin)
                
                db.session.commit()
                logger.debug(f"Plugin {plugin_info['name']} enregistré en DB")
                
        except Exception as e:
            db.session.rollback()
            logger.opt(exception=e).error(
                "Erreur DB pour plugin {}: {}",
                plugin_info.get('name', 'unknown'),
                e,
            )
    
    def _update_plugin_fields(self, plugin: Plugin, plugin_info: Dict) -> None:
        """
        Met à jour les champs d'un plugin existant.
        
        Args:
            plugin (Plugin): Instance du plugin à mettre à jour
            plugin_info (Dict): Nouvelles informations
        """
        plugin.version = plugin_info['version']
        plugin.plugin_api_version = plugin_info.get('plugin_api_version', '2.0')
        plugin.description = plugin_info.get('description', '')
        plugin.author = plugin_info.get('author', '')
        plugin.plugin_type = plugin_info['plugin_type']
        plugin.source = plugin_info['source']
        plugin.path = plugin_info['path']
        plugin.entry_point = plugin_info['entry_point']
        plugin.categories = plugin_info.get('categories', [])
        plugin.input_types = plugin_info.get('input_types', {})
        plugin.heavy_cpu = plugin_info.get('heavy_cpu', False)
        plugin.needs_network = plugin_info.get('needs_network', False)
        plugin.needs_filesystem = plugin_info.get('needs_filesystem', False)
        plugin.metadata_json = json.dumps(plugin_info)
    
    def _cleanup_deleted_plugins(self, discovered_paths: set) -> None:
        """
        Supprime de la base de données les plugins qui n'existent plus physiquement.
        
        Args:
            discovered_paths (set): Ensemble des chemins de plugins découverts
        """
        try:
            all_plugins = Plugin.query.all()
            deleted_count = 0
            
            for plugin in all_plugins:
                if plugin.path not in discovered_paths:
                    logger.info(
                        f"Suppression plugin {plugin.name} (dossier introuvable: {plugin.path})"
                    )
                    db.session.delete(plugin)
                    deleted_count += 1
            
            if deleted_count > 0:
                db.session.commit()
                logger.info(f"{deleted_count} plugin(s) supprimé(s) de la DB")
                
        except Exception as e:
            db.session.rollback()
            logger.error(f"Erreur lors du nettoyage des plugins: {e}")
    
    # =========================================================================
    # Chargement du schéma
    # =========================================================================
    
    def _load_schema(self) -> None:
        """
        Charge le schéma JSON de validation depuis le fichier plugin.schema.json.
        """
        try:
            schema_path = (
                Path(__file__).parent / 'schemas' / 'plugin.schema.json'
            )
            
            if not schema_path.exists():
                logger.warning(
                    f"Schéma de validation non trouvé: {schema_path}"
                )
                return
            
            with open(schema_path, 'r', encoding='utf-8') as f:
                self._schema = json.load(f)
            
            logger.debug("Schéma de validation chargé avec succès")
            
        except Exception as e:
            logger.error(f"Erreur lors du chargement du schéma: {e}")
            self._schema = None
    
    # =========================================================================
    # Utilitaires
    # =========================================================================
    
    def get_plugin_info(self, plugin_name: str) -> Optional[Dict]:
        """
        Récupère les informations d'un plugin depuis la base de données.
        
        Args:
            plugin_name (str): Nom du plugin
            
        Returns:
            Optional[Dict]: Informations du plugin ou None si non trouvé
        """
        if not self.app:
            return None
        
        try:
            with self.app.app_context():
                plugin = Plugin.query.filter_by(name=plugin_name).first()
                
                if plugin:
                    return plugin.to_dict(include_metadata=True)
                
                logger.warning(f"Plugin {plugin_name} non trouvé en DB")
                return None
                
        except Exception as e:
            logger.error(f"Erreur récupération plugin {plugin_name}: {e}")
            return None
    
    def list_plugins(
        self,
        source: Optional[str] = None,
        category: Optional[str] = None,
        enabled_only: bool = True
    ) -> List[Dict]:
        """
        Liste les plugins disponibles avec filtres optionnels.
        
        Args:
            source (str, optional): Filtrer par source ('official', 'custom')
            category (str, optional): Filtrer par catégorie
            enabled_only (bool): Ne retourner que les plugins activés
            
        Returns:
            List[Dict]: Liste des plugins correspondant aux critères
        """
        if not self.app:
            return []
        
        try:
            with self.app.app_context():
                query = Plugin.query
                
                if source:
                    query = query.filter_by(source=source)
                
                if enabled_only:
                    query = query.filter_by(enabled=True)
                
                plugins = query.all()
                
                # Filtrer par catégorie si spécifié
                if category:
                    plugins = [
                        p for p in plugins
                        if category in (p.categories or [])
                    ]
                
                return [p.to_dict() for p in plugins]
                
        except Exception as e:
            logger.error(f"Erreur listage plugins: {e}")
            return []
    
    def get_discovery_errors(self) -> Dict[str, str]:
        """
        Retourne les erreurs de découverte de plugins.
        
        Returns:
            Dict[str, str]: Dictionnaire {path: error_message}
        """
        return self._loading_errors.copy()
    
    def clear_errors(self) -> None:
        """Efface les erreurs de chargement."""
        self._loading_errors.clear()
    
    # =========================================================================
    # Chargement et exécution de plugins
    # =========================================================================
    
    def get_plugin(
        self,
        plugin_name: str,
        force_reload: bool = False
    ) -> Optional[PluginInterface]:
        """
        Récupère un plugin par son nom, le charge si nécessaire (lazy loading).
        
        Args:
            plugin_name (str): Nom du plugin
            force_reload (bool): Force le rechargement même si déjà chargé
            
        Returns:
            Optional[PluginInterface]: Instance du wrapper du plugin ou None si erreur
        """
        # Si déjà chargé et pas de force_reload, retourner du cache
        if not force_reload and plugin_name in self.loaded_plugins:
            logger.debug(f"Plugin {plugin_name} récupéré du cache")
            return self.loaded_plugins[plugin_name]
        
        if not self.app:
            logger.error("Pas d'app Flask, impossible de charger le plugin")
            return None
        
        try:
            with self.app.app_context():
                # Récupérer les informations du plugin depuis la DB
                plugin_record = Plugin.query.filter_by(name=plugin_name).first()
                
                if not plugin_record:
                    logger.error(f"Plugin {plugin_name} non trouvé en DB")
                    return None
                
                if not plugin_record.enabled:
                    logger.warning(f"Plugin {plugin_name} est désactivé")
                    return None
                
                # Créer les métadonnées
                metadata = PluginMetadata(
                    name=plugin_record.name,
                    version=plugin_record.version,
                    plugin_type=PluginType(plugin_record.plugin_type),
                    entry_point=plugin_record.entry_point,
                    path=plugin_record.path,
                    timeout_seconds=self._get_timeout_from_metadata(plugin_record)
                )
                
                # Créer le wrapper approprié
                wrapper = create_plugin_wrapper(
                    plugin_record.plugin_type,
                    metadata,
                    plugin_manager=self
                )
                
                if not wrapper:
                    logger.error(
                        f"Impossible de créer wrapper pour {plugin_name} "
                        f"(type: {plugin_record.plugin_type})"
                    )
                    return None
                
                # Initialiser le plugin
                if not wrapper.initialize():
                    logger.error(f"Échec d'initialisation du plugin {plugin_name}")
                    self._loading_errors[plugin_name] = "Échec d'initialisation"
                    return None
                
                # Mettre en cache
                self.loaded_plugins[plugin_name] = wrapper
                
                # Effacer l'erreur si elle existait
                self._loading_errors.pop(plugin_name, None)
                
                logger.info(f"Plugin {plugin_name} chargé avec succès")
                
                return wrapper
                
        except Exception as e:
            logger.opt(exception=e).error(
                "Erreur lors du chargement du plugin {}: {}",
                plugin_name,
                e,
            )
            self._loading_errors[plugin_name] = str(e)
            return None
    
    def execute_plugin(
        self,
        plugin_name: str,
        inputs: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Exécute un plugin avec les inputs fournis.
        
        Args:
            plugin_name (str): Nom du plugin à exécuter
            inputs (dict): Paramètres d'entrée pour le plugin
            
        Returns:
            Optional[Dict]: Résultat de l'exécution au format standardisé ou None
        """
        # Récupérer le plugin (lazy loading)
        plugin = self.get_plugin(plugin_name)
        
        if not plugin:
            logger.error(f"Plugin {plugin_name} non disponible pour exécution")
            return {
                "status": "error",
                "summary": f"Plugin {plugin_name} non disponible",
                "results": [],
                "plugin_info": {
                    "name": plugin_name,
                    "version": "unknown",
                    "execution_time_ms": 0
                }
            }
        
        try:
            logger.info(f"Exécution du plugin {plugin_name}")
            
            # Exécuter le plugin
            result = plugin.execute(inputs)
            
            logger.info(
                f"Plugin {plugin_name} exécuté avec succès "
                f"(status: {result.get('status')})"
            )
            
            return result
            
        except Exception as e:
            logger.opt(exception=e).error(
                "Erreur lors de l'exécution du plugin {}: {}",
                plugin_name,
                e,
            )
            return {
                "status": "error",
                "summary": f"Erreur d'exécution: {str(e)}",
                "results": [],
                "plugin_info": {
                    "name": plugin_name,
                    "version": "unknown",
                    "execution_time_ms": 0
                },
                "error": {
                    "type": type(e).__name__,
                    "message": str(e)
                }
            }
    
    def unload_plugin(self, plugin_name: str) -> bool:
        """
        Décharge un plugin de la mémoire.
        
        Args:
            plugin_name (str): Nom du plugin à décharger
            
        Returns:
            bool: True si le déchargement a réussi
        """
        if plugin_name not in self.loaded_plugins:
            logger.debug(f"Plugin {plugin_name} n'est pas chargé")
            return True
        
        try:
            plugin = self.loaded_plugins[plugin_name]
            plugin.cleanup()
            
            del self.loaded_plugins[plugin_name]
            
            logger.info(f"Plugin {plugin_name} déchargé")
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors du déchargement du plugin {plugin_name}: {e}")
            return False
    
    def unload_all_plugins(self) -> None:
        """Décharge tous les plugins chargés."""
        plugin_names = list(self.loaded_plugins.keys())
        
        for plugin_name in plugin_names:
            self.unload_plugin(plugin_name)
        
        logger.info(f"{len(plugin_names)} plugin(s) déchargé(s)")

    def preload_enabled_plugins(self) -> int:
        """
        Précharge les plugins actifs lorsque le lazy mode est désactivé.
        """
        if not self.app:
            return 0

        count = 0
        try:
            with self.app.app_context():
                plugin_names = [
                    plugin.name
                    for plugin in Plugin.query.filter_by(enabled=True).all()
                ]

            for plugin_name in plugin_names:
                if self.get_plugin(plugin_name):
                    count += 1

            logger.info("Préchargement de %d plugin(s)", count)
        except Exception as error:
            logger.error("Erreur lors du préchargement des plugins: %s", error)

        return count
    
    def reload_plugin(self, plugin_name: str) -> bool:
        """
        Recharge un plugin (décharge puis recharge).
        
        Args:
            plugin_name (str): Nom du plugin à recharger
            
        Returns:
            bool: True si le rechargement a réussi
        """
        logger.info(f"Rechargement du plugin {plugin_name}")
        
        # Décharger
        self.unload_plugin(plugin_name)
        
        # Recharger
        plugin = self.get_plugin(plugin_name, force_reload=True)
        
        return plugin is not None

    def reload_all_plugins(self) -> int:
        """
        Recharge tous les plugins actuellement chargés en mémoire.
        
        Returns:
            int: Nombre de plugins rechargés avec succès
        """
        plugin_names = list(self.loaded_plugins.keys())
        success_count = 0
        
        logger.info(f"Début du rechargement de {len(plugin_names)} plugins")
        
        for name in plugin_names:
            if self.reload_plugin(name):
                success_count += 1
                
        logger.info(f"Rechargement terminé: {success_count}/{len(plugin_names)} plugins rechargés")
        return success_count
    
    def _get_timeout_from_metadata(self, plugin_record: Plugin) -> int:
        """
        Extrait le timeout du metadata_json du plugin.
        
        Args:
            plugin_record (Plugin): Enregistrement du plugin
            
        Returns:
            int: Timeout en secondes (défaut: 30)
        """
        default_timeout = getattr(self, 'default_timeout', 60)
        allow_long_running = getattr(self, 'allow_long_running', False)
        try:
            if plugin_record.metadata_json:
                metadata = json.loads(plugin_record.metadata_json)
                timeout = metadata.get('timeout_seconds', default_timeout)
                if not allow_long_running:
                    return min(timeout, default_timeout)
                return timeout
        except Exception:
            pass
        
        return default_timeout
    
    # =========================================================================
    # Gestion du statut
    # =========================================================================
    
    def get_plugin_status(self) -> Dict[str, Dict]:
        """
        Retourne l'état actuel de tous les plugins.
        
        Returns:
            Dict[str, Dict]: État de chaque plugin {name: {enabled, loaded, error}}
        """
        if not self.app:
            return {}
        
        status = {}
        
        try:
            with self.app.app_context():
                all_plugins = Plugin.query.all()
                
                for plugin in all_plugins:
                    status[plugin.name] = {
                        "enabled": plugin.enabled,
                        "loaded": plugin.name in self.loaded_plugins,
                        "error": self._loading_errors.get(plugin.name),
                        "version": plugin.version,
                        "source": plugin.source,
                        "plugin_type": plugin.plugin_type
                    }
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du statut: {e}")
        
        return status
    
    def __repr__(self):
        return (
            f"<PluginManager plugins_dir={self.plugins_dir} "
            f"loaded={len(self.loaded_plugins)}>"
        )

    def _load_runtime_preferences(self) -> None:
        if not self.app:
            return

        try:
            with self.app.app_context():
                self.lazy_mode = bool(get_value_or_default('geoApp.plugins.lazyMode', True))
                self.default_timeout = int(get_value_or_default('geoApp.plugins.executor.timeoutSec', 60))
                self.allow_long_running = bool(get_value_or_default('geoApp.plugins.executor.allowLongRunning', False))
                logger.info(
                    "Préférences plugins -> lazy_mode=%s timeout=%ss allow_long_running=%s",
                    self.lazy_mode,
                    self.default_timeout,
                    self.allow_long_running
                )
        except Exception as error:
            logger.warning("Impossible de charger les préférences plugins: %s", error)
