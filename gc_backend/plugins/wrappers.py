"""
Wrappers d'exécution pour différents types de plugins.

Ce module fournit les wrappers nécessaires pour charger et exécuter
différents types de plugins (Python, Binary, etc.).

Chaque wrapper implémente l'interface PluginInterface et gère :
- L'initialisation/chargement du plugin
- L'exécution avec timeout
- Le nettoyage des ressources
- La gestion des erreurs
"""

import os
import sys
import json
import subprocess
import importlib.util
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

from loguru import logger


# =============================================================================
# Types et énumérations
# =============================================================================

class PluginType(Enum):
    """Types de plugins supportés."""
    PYTHON = "python"
    RUST = "rust"
    BINARY = "binary"
    WASM = "wasm"
    NODE = "node"


@dataclass
class PluginMetadata:
    """
    Métadonnées d'un plugin.
    
    Attributes:
        name (str): Nom du plugin
        version (str): Version du plugin
        plugin_type (PluginType): Type de plugin
        entry_point (str): Point d'entrée (fichier à charger/exécuter)
        path (str): Chemin vers le dossier du plugin
        timeout_seconds (int): Timeout d'exécution en secondes
    """
    name: str
    version: str
    plugin_type: PluginType
    entry_point: str
    path: str
    timeout_seconds: int = 30


# =============================================================================
# Interface de base pour tous les plugins
# =============================================================================

class PluginInterface(ABC):
    """
    Interface abstraite que tous les wrappers de plugins doivent implémenter.
    
    Cette interface garantit que tous les plugins, quel que soit leur type,
    peuvent être chargés, exécutés et nettoyés de manière uniforme.
    """
    
    @abstractmethod
    def initialize(self) -> bool:
        """
        Initialise le plugin (charge les ressources, import, etc.).
        
        Cette méthode est appelée une seule fois lors du premier chargement
        du plugin. Elle doit préparer toutes les ressources nécessaires
        à l'exécution.
        
        Returns:
            bool: True si l'initialisation a réussi, False sinon
        """
        pass
    
    @abstractmethod
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute la logique principale du plugin.
        
        Args:
            inputs (dict): Paramètres d'entrée pour le plugin
            
        Returns:
            dict: Résultat de l'exécution au format standardisé
            
        Raises:
            RuntimeError: Si le plugin n'est pas initialisé
            TimeoutError: Si l'exécution dépasse le timeout
        """
        pass
    
    @abstractmethod
    def cleanup(self) -> bool:
        """
        Libère les ressources utilisées par le plugin.
        
        Cette méthode est appelée lors du déchargement du plugin
        ou de l'arrêt de l'application.
        
        Returns:
            bool: True si le nettoyage a réussi, False sinon
        """
        pass


# =============================================================================
# Wrapper pour plugins Python
# =============================================================================

class PythonPluginWrapper(PluginInterface):
    """
    Wrapper pour exécuter des plugins Python.
    
    Ce wrapper gère :
    - L'import dynamique du module Python
    - L'instanciation de la classe du plugin
    - L'exécution avec timeout (optionnel)
    - La gestion des erreurs d'import et d'exécution
    
    Attributes:
        metadata (PluginMetadata): Métadonnées du plugin
        plugin_manager: Référence au PluginManager (pour injection)
        _module: Module Python importé
        _instance: Instance de la classe du plugin
    """
    
    def __init__(
        self,
        metadata: PluginMetadata,
        plugin_manager=None
    ):
        """
        Initialise le wrapper Python.
        
        Args:
            metadata (PluginMetadata): Métadonnées du plugin
            plugin_manager: Instance du PluginManager (optionnel)
        """
        self.metadata = metadata
        self.plugin_manager = plugin_manager
        self._module = None
        self._instance = None
        
        logger.debug(f"PythonPluginWrapper créé pour {metadata.name}")
    
    def initialize(self) -> bool:
        """
        Charge le module Python et instancie la classe du plugin.
        
        Le wrapper cherche une classe dont le nom se termine par 'Plugin'.
        Si plusieurs classes correspondent, la première trouvée est utilisée.
        
        Returns:
            bool: True si l'initialisation a réussi, False sinon
        """
        try:
            entry_file = Path(self.metadata.path) / self.metadata.entry_point
            
            if not entry_file.exists():
                logger.error(f"Fichier d'entrée non trouvé: {entry_file}")
                return False
            
            # Importer le module dynamiquement
            spec = importlib.util.spec_from_file_location(
                self.metadata.name,
                str(entry_file)
            )
            
            if spec is None or spec.loader is None:
                logger.error(f"Impossible de créer spec pour {entry_file}")
                return False
            
            self._module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = self._module
            
            # Ajouter le chemin du plugin au sys.path temporairement
            plugin_dir = str(Path(self.metadata.path))
            if plugin_dir not in sys.path:
                sys.path.insert(0, plugin_dir)
            
            try:
                spec.loader.exec_module(self._module)
            finally:
                # Retirer le chemin après le chargement
                if plugin_dir in sys.path:
                    sys.path.remove(plugin_dir)
            
            # Chercher la classe du plugin
            plugin_class = self._find_plugin_class()
            
            if plugin_class is None:
                logger.error(
                    f"Aucune classe de plugin trouvée dans {entry_file}"
                )
                return False
            
            # Instancier le plugin
            self._instance = plugin_class()
            
            # Injecter le plugin_manager si le plugin le supporte
            if hasattr(self._instance, 'set_plugin_manager') and self.plugin_manager:
                self._instance.set_plugin_manager(self.plugin_manager)
            
            logger.info(
                f"Plugin {self.metadata.name} initialisé avec succès "
                f"(classe: {plugin_class.__name__})"
            )
            
            return True
            
        except Exception as e:
            logger.error(
                f"Erreur lors de l'initialisation du plugin {self.metadata.name}: {e}",
                exc_info=True
            )
            return False
    
    def _find_plugin_class(self):
        """
        Trouve la classe du plugin dans le module.
        
        Cherche d'abord une classe nommée '{PluginName}Plugin',
        puis toute classe se terminant par 'Plugin'.
        
        Returns:
            type: Classe du plugin ou None si non trouvée
        """
        if not self._module:
            return None
        
        # Convention de nommage : {PluginName}Plugin
        # Ex: caesar → CaesarPlugin
        expected_class_name = f"{self.metadata.name.replace('_', '').title()}Plugin"
        
        # Chercher d'abord par nom conventionnel
        if hasattr(self._module, expected_class_name):
            cls = getattr(self._module, expected_class_name)
            if isinstance(cls, type):
                logger.debug(f"Classe trouvée par convention: {expected_class_name}")
                return cls
        
        # Sinon, chercher toute classe se terminant par 'Plugin'
        for attr_name in dir(self._module):
            if attr_name.endswith('Plugin'):
                cls = getattr(self._module, attr_name)
                if isinstance(cls, type):
                    logger.debug(f"Classe trouvée: {attr_name}")
                    return cls
        
        return None
    
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute la méthode execute() du plugin.
        
        Args:
            inputs (dict): Paramètres d'entrée
            
        Returns:
            dict: Résultat de l'exécution au format standardisé
            
        Raises:
            RuntimeError: Si le plugin n'est pas initialisé
            NotImplementedError: Si le plugin n'implémente pas execute()
        """
        if not self._instance:
            raise RuntimeError(
                f"Plugin {self.metadata.name} non initialisé. "
                "Appelez initialize() d'abord."
            )
        
        if not hasattr(self._instance, 'execute'):
            raise NotImplementedError(
                f"Le plugin {self.metadata.name} n'implémente pas la méthode execute()"
            )
        
        try:
            logger.debug(
                f"Exécution du plugin {self.metadata.name} avec inputs: "
                f"{list(inputs.keys())}"
            )
            
            result = self._instance.execute(inputs)
            
            logger.debug(
                f"Plugin {self.metadata.name} exécuté avec succès "
                f"(status: {result.get('status')})"
            )
            
            return result
            
        except Exception as e:
            logger.error(
                f"Erreur lors de l'exécution du plugin {self.metadata.name}: {e}",
                exc_info=True
            )
            
            # Retourner une erreur au format standardisé
            return {
                "status": "error",
                "summary": f"Erreur d'exécution: {str(e)}",
                "results": [],
                "plugin_info": {
                    "name": self.metadata.name,
                    "version": self.metadata.version,
                    "execution_time_ms": 0
                },
                "error": {
                    "type": type(e).__name__,
                    "message": str(e)
                }
            }
    
    def cleanup(self) -> bool:
        """
        Nettoie les ressources du plugin.
        
        Returns:
            bool: True si le nettoyage a réussi
        """
        try:
            # Appeler cleanup() si le plugin l'implémente
            if self._instance and hasattr(self._instance, 'cleanup'):
                self._instance.cleanup()
            
            self._module = None
            self._instance = None
            
            logger.debug(f"Plugin {self.metadata.name} nettoyé")
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage du plugin {self.metadata.name}: {e}")
            return False


# =============================================================================
# Wrapper pour plugins binaires
# =============================================================================

class BinaryPluginWrapper(PluginInterface):
    """
    Wrapper pour exécuter des plugins binaires (exécutables).
    
    Ce wrapper gère :
    - L'exécution via subprocess
    - La communication JSON (stdin/stdout)
    - Les timeouts d'exécution
    - La gestion des codes de retour
    
    Le plugin binaire doit :
    - Lire les inputs JSON depuis stdin
    - Écrire le résultat JSON sur stdout
    - Retourner code 0 si succès, non-0 si erreur
    
    Attributes:
        metadata (PluginMetadata): Métadonnées du plugin
        binary_path (Path): Chemin vers l'exécutable
    """
    
    def __init__(self, metadata: PluginMetadata):
        """
        Initialise le wrapper binaire.
        
        Args:
            metadata (PluginMetadata): Métadonnées du plugin
        """
        self.metadata = metadata
        self.binary_path = Path(metadata.path) / metadata.entry_point
        
        logger.debug(f"BinaryPluginWrapper créé pour {metadata.name}")
    
    def initialize(self) -> bool:
        """
        Vérifie que le binaire existe et est exécutable.
        
        Returns:
            bool: True si le binaire est accessible et exécutable
        """
        try:
            if not self.binary_path.exists():
                logger.error(f"Binaire non trouvé: {self.binary_path}")
                return False
            
            # Sur Windows, vérifier l'extension
            if sys.platform == 'win32':
                valid_extensions = ['.exe', '.bat', '.cmd']
                if not any(str(self.binary_path).lower().endswith(ext) 
                          for ext in valid_extensions):
                    logger.warning(
                        f"Extension binaire inhabituelle sur Windows: {self.binary_path}"
                    )
            
            # Sur Unix, vérifier les permissions d'exécution
            elif not os.access(self.binary_path, os.X_OK):
                logger.error(
                    f"Binaire non exécutable: {self.binary_path}. "
                    "Permissions incorrectes."
                )
                return False
            
            logger.info(f"Plugin binaire {self.metadata.name} validé")
            
            return True
            
        except Exception as e:
            logger.error(
                f"Erreur lors de la validation du binaire {self.metadata.name}: {e}"
            )
            return False
    
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exécute le plugin binaire via subprocess.
        
        Args:
            inputs (dict): Paramètres d'entrée (sérialisés en JSON)
            
        Returns:
            dict: Résultat désérialisé depuis stdout
            
        Raises:
            RuntimeError: Si l'exécution échoue
            TimeoutError: Si le timeout est dépassé
        """
        try:
            # Sérialiser les inputs en JSON
            input_json = json.dumps(inputs).encode('utf-8')
            
            logger.debug(
                f"Exécution binaire {self.metadata.name} "
                f"(timeout: {self.metadata.timeout_seconds}s)"
            )
            
            # Exécuter le binaire
            process = subprocess.Popen(
                [str(self.binary_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path(self.metadata.path))
            )
            
            # Communiquer avec timeout
            try:
                stdout, stderr = process.communicate(
                    input_json,
                    timeout=self.metadata.timeout_seconds
                )
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                raise TimeoutError(
                    f"Plugin {self.metadata.name} a dépassé le timeout "
                    f"de {self.metadata.timeout_seconds}s"
                )
            
            # Vérifier le code de retour
            if process.returncode != 0:
                error_msg = stderr.decode('utf-8', errors='replace')
                logger.error(
                    f"Plugin binaire {self.metadata.name} a échoué "
                    f"(code: {process.returncode}): {error_msg}"
                )
                raise RuntimeError(
                    f"Erreur d'exécution (code {process.returncode}): {error_msg}"
                )
            
            # Désérialiser la sortie
            result = json.loads(stdout.decode('utf-8'))
            
            logger.debug(
                f"Plugin binaire {self.metadata.name} exécuté avec succès"
            )
            
            return result
            
        except json.JSONDecodeError as e:
            logger.error(
                f"Sortie JSON invalide du plugin {self.metadata.name}: {e}"
            )
            return {
                "status": "error",
                "summary": f"Sortie JSON invalide: {str(e)}",
                "results": [],
                "plugin_info": {
                    "name": self.metadata.name,
                    "version": self.metadata.version,
                    "execution_time_ms": 0
                }
            }
        except Exception as e:
            logger.error(
                f"Erreur lors de l'exécution du binaire {self.metadata.name}: {e}",
                exc_info=True
            )
            return {
                "status": "error",
                "summary": f"Erreur d'exécution: {str(e)}",
                "results": [],
                "plugin_info": {
                    "name": self.metadata.name,
                    "version": self.metadata.version,
                    "execution_time_ms": 0
                }
            }
    
    def cleanup(self) -> bool:
        """
        Nettoie les ressources (rien à faire pour un binaire).
        
        Returns:
            bool: True
        """
        logger.debug(f"Plugin binaire {self.metadata.name} nettoyé")
        return True


# =============================================================================
# Factory pour créer les wrappers appropriés
# =============================================================================

def create_plugin_wrapper(
    plugin_type: str,
    metadata: PluginMetadata,
    plugin_manager=None
) -> Optional[PluginInterface]:
    """
    Factory pour créer le wrapper approprié selon le type de plugin.
    
    Args:
        plugin_type (str): Type de plugin ('python', 'binary', etc.)
        metadata (PluginMetadata): Métadonnées du plugin
        plugin_manager: Instance du PluginManager (optionnel)
        
    Returns:
        Optional[PluginInterface]: Wrapper créé ou None si type non supporté
    """
    try:
        plugin_type_enum = PluginType(plugin_type.lower())
        
        if plugin_type_enum == PluginType.PYTHON:
            return PythonPluginWrapper(metadata, plugin_manager)
        
        elif plugin_type_enum == PluginType.BINARY:
            return BinaryPluginWrapper(metadata)
        
        elif plugin_type_enum in [PluginType.RUST, PluginType.WASM, PluginType.NODE]:
            logger.warning(
                f"Type de plugin {plugin_type} pas encore implémenté. "
                "Utilisation du wrapper binaire par défaut."
            )
            return BinaryPluginWrapper(metadata)
        
        else:
            logger.error(f"Type de plugin non supporté: {plugin_type}")
            return None
            
    except ValueError:
        logger.error(f"Type de plugin invalide: {plugin_type}")
        return None
