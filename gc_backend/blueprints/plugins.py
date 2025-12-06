"""
Blueprint pour les endpoints API des plugins.

Ce module expose les routes REST pour :
- Lister les plugins disponibles
- Récupérer les informations d'un plugin
- Générer l'interface HTML d'un plugin
- Exécuter un plugin (mode synchrone)
- Exécuter des plugins en mode batch
- Redéclencher la découverte de plugins
"""

import uuid
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, jsonify, request, render_template_string, current_app
from loguru import logger
from typing import Dict, Any, List, Optional

from ..plugins import PluginManager
from ..database import db
from ..geocaches.models import Geocache


# Créer le blueprint
bp = Blueprint('plugins', __name__, url_prefix='/api/plugins')

# Instance globale du PluginManager (sera initialisée dans create_app)
_plugin_manager: PluginManager = None


def init_plugin_manager(manager: PluginManager):
    """
    Initialise le PluginManager global pour ce blueprint.
    
    Cette fonction doit être appelée depuis create_app() après
    la création du PluginManager.
    
    Args:
        manager (PluginManager): Instance du gestionnaire de plugins
    """
    global _plugin_manager
    _plugin_manager = manager
    logger.info("PluginManager initialisé dans le blueprint plugins")


# Stockage des tâches batch en mémoire (en production, utiliser Redis ou une base de données)
batch_tasks: Dict[str, 'BatchPluginTask'] = {}


class BatchPluginTask:
    """
    Classe pour gérer l'exécution batch d'un plugin sur plusieurs géocaches.
    """
    
    def __init__(self, task_id: str, plugin_name: str, geocaches: List[Dict], 
                 inputs: Dict[str, Any], execution_mode: str = 'sequential',
                 max_concurrency: int = 3, detect_coordinates: bool = True):
        self.task_id = task_id
        self.plugin_name = plugin_name
        self.geocaches = geocaches
        self.inputs = inputs
        self.execution_mode = execution_mode
        self.max_concurrency = max_concurrency
        self.detect_coordinates = detect_coordinates
        
        # État de la tâche
        self.status = 'pending'  # pending, running, completed, failed, cancelled
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.cancelled = False
        
        # Résultats par géocache
        self.results: List[Dict] = []
        for geocache in geocaches:
            self.results.append({
                'geocache_id': geocache['id'],
                'gc_code': geocache['gc_code'],
                'name': geocache['name'],
                'status': 'pending',
                'result': None,
                'error': None,
                'execution_time': None,
                'coordinates': None,
                'started_at': None,
                'completed_at': None
            })
    
    def execute(self):
        """
        Exécute la tâche batch selon le mode configuré.
        """
        try:
            self.status = 'running'
            self.started_at = datetime.utcnow()
            
            logger.info(f"Starting batch task {self.task_id}: {self.plugin_name} on {len(self.geocaches)} geocaches")
            
            if self.execution_mode == 'sequential':
                self._execute_sequential()
            else:
                self._execute_parallel()
            
            if not self.cancelled:
                self.status = 'completed'
                logger.info(f"Batch task {self.task_id} completed successfully")
            
        except Exception as e:
            self.status = 'failed'
            logger.error(f"Batch task {self.task_id} failed: {str(e)}")
        finally:
            self.completed_at = datetime.utcnow()
    
    def _execute_sequential(self):
        """
        Exécution séquentielle des géocaches.
        """
        for i, geocache in enumerate(self.geocaches):
            if self.cancelled:
                break
            
            result = self.results[i]
            result['status'] = 'executing'
            result['started_at'] = datetime.utcnow()
            
            try:
                start_time = time.time()
                
                # Préparer les inputs pour cette géocache
                geocache_inputs = self._prepare_inputs_for_geocache(geocache)
                
                # Exécuter le plugin
                plugin_manager = get_plugin_manager()
                plugin_result = plugin_manager.execute_plugin(
                    self.plugin_name, 
                    geocache_inputs
                )
                
                execution_time = (time.time() - start_time) * 1000  # en ms
                
                # Traiter les résultats
                processed_result = self._process_plugin_result(plugin_result, geocache)
                
                result.update({
                    'status': 'completed',
                    'result': plugin_result,
                    'coordinates': processed_result.get('coordinates'),
                    'execution_time': execution_time,
                    'completed_at': datetime.utcnow()
                })
                
            except Exception as e:
                result.update({
                    'status': 'error',
                    'error': str(e),
                    'completed_at': datetime.utcnow()
                })
                logger.error(f"Error executing plugin on {geocache['gc_code']}: {str(e)}")
    
    def _execute_parallel(self):
        """
        Exécution parallèle des géocaches avec ThreadPoolExecutor.
        """
        def execute_single_geocache(geocache_data):
            geocache, result_index = geocache_data
            result = self.results[result_index]
            
            if self.cancelled:
                return result
            
            result['status'] = 'executing'
            result['started_at'] = datetime.utcnow()
            
            try:
                start_time = time.time()
                
                # Préparer les inputs pour cette géocache
                geocache_inputs = self._prepare_inputs_for_geocache(geocache)
                
                # Exécuter le plugin
                plugin_manager = get_plugin_manager()
                plugin_result = plugin_manager.execute_plugin(
                    self.plugin_name, 
                    geocache_inputs
                )
                
                execution_time = (time.time() - start_time) * 1000  # en ms
                
                # Traiter les résultats
                processed_result = self._process_plugin_result(plugin_result, geocache)
                
                result.update({
                    'status': 'completed',
                    'result': plugin_result,
                    'coordinates': processed_result.get('coordinates'),
                    'execution_time': execution_time,
                    'completed_at': datetime.utcnow()
                })
                
            except Exception as e:
                result.update({
                    'status': 'error',
                    'error': str(e),
                    'completed_at': datetime.utcnow()
                })
                logger.error(f"Error executing plugin on {geocache['gc_code']}: {str(e)}")
            
            return result
        
        # Exécuter en parallèle avec ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            # Soumettre toutes les tâches
            future_to_index = {
                executor.submit(execute_single_geocache, (geocache, i)): i 
                for i, geocache in enumerate(self.geocaches)
            }
            
            # Traiter les résultats au fur et à mesure
            for future in as_completed(future_to_index):
                if self.cancelled:
                    break
                try:
                    future.result()
                except Exception as e:
                    index = future_to_index[future]
                    self.results[index].update({
                        'status': 'error',
                        'error': str(e),
                        'completed_at': datetime.utcnow()
                    })
    
    def _prepare_inputs_for_geocache(self, geocache: Dict) -> Dict[str, Any]:
        """
        Prépare les inputs du plugin pour une géocache spécifique.
        """
        inputs = self.inputs.copy()
        
        # Injecter les données spécifiques à la géocache
        plugin_manager = get_plugin_manager()
        plugin_info = plugin_manager.get_plugin_info(self.plugin_name)
        if plugin_info and 'metadata' in plugin_info and 'input_types' in plugin_info['metadata']:
            input_types = plugin_info['metadata']['input_types']
            
            for key, input_type in input_types.items():
                default_value_source = input_type.get('default_value_source')
                
                if default_value_source == 'geocache_id':
                    inputs[key] = geocache['gc_code']
                elif default_value_source == 'geocache_description' and geocache.get('description'):
                    inputs[key] = geocache['description']
                elif default_value_source == 'geocache_coordinates' and geocache.get('coordinates'):
                    coords = geocache['coordinates']
                    inputs[key] = coords.get('coordinates_raw') or f"{coords['latitude']}, {coords['longitude']}"
        
        return inputs
    
    def _process_plugin_result(self, plugin_result: Dict, geocache: Dict) -> Dict:
        """
        Traite les résultats du plugin (détection de coordonnées, etc.).
        """
        processed = {}
        
        if self.detect_coordinates and plugin_result.get('results'):
            for item in plugin_result['results']:
                text_output = item.get('text_output')
                if text_output:
                    try:
                        # Utiliser directement la fonction de détection (pas l'API)
                        from gc_backend.blueprints.coordinates import detect_gps_coordinates
                        
                        logger.info(f"[Batch] Détection de coordonnées dans: {text_output[:100]}...")
                        
                        coords = detect_gps_coordinates(text_output, include_numeric_only=False)
                        
                        if coords.get('exist'):
                            logger.info(f"[Batch] Coordonnées trouvées: {coords.get('ddm')}")
                            processed['coordinates'] = {
                                'latitude': coords.get('decimal_latitude', 0),
                                'longitude': coords.get('decimal_longitude', 0),
                                'formatted': coords.get('ddm', '')
                            }
                            break
                        else:
                            logger.info(f"[Batch] Aucune coordonnée détectée dans ce résultat")
                    except Exception as e:
                        logger.warning(f"Error detecting coordinates: {str(e)}")
                        import traceback
                        traceback.print_exc()
        
        return processed
    
    def cancel(self):
        """
        Annule la tâche.
        """
        self.cancelled = True
        if self.status == 'running':
            self.status = 'cancelled'
        logger.info(f"Batch task {self.task_id} cancellation requested")
    
    def get_status(self) -> Dict:
        """
        Retourne le statut actuel de la tâche.
        """
        completed_count = len([r for r in self.results if r['status'] == 'completed'])
        error_count = len([r for r in self.results if r['status'] == 'error'])
        total_count = len(self.results)
        
        progress_percentage = (completed_count / total_count * 100) if total_count > 0 else 0
        
        return {
            'task_id': self.task_id,
            'plugin_name': self.plugin_name,
            'status': self.status,
            'progress': {
                'completed': completed_count,
                'errors': error_count,
                'total': total_count,
                'percentage': round(progress_percentage, 1)
            },
            'results': self.results,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'execution_mode': self.execution_mode,
            'cancelled': self.cancelled
        }


def get_plugin_manager() -> PluginManager:
    """
    Récupère l'instance du PluginManager.
    
    Returns:
        PluginManager: Instance du gestionnaire
        
    Raises:
        RuntimeError: Si le manager n'est pas initialisé
    """
    if _plugin_manager is None:
        raise RuntimeError(
            "PluginManager non initialisé. "
            "Appelez init_plugin_manager() depuis create_app()"
        )
    return _plugin_manager


# =============================================================================
# Routes de listage et informations
# =============================================================================

@bp.route('', methods=['GET'])
def list_plugins():
    """
    Liste tous les plugins disponibles avec filtres optionnels.
    
    Query Parameters:
        source (str, optional): Filtrer par source ('official', 'custom')
        category (str, optional): Filtrer par catégorie
        enabled (bool, optional): Filtrer par statut (true/false)
        
    Returns:
        JSON: {
            "plugins": [liste des plugins],
            "total": nombre total,
            "filters": filtres appliqués
        }
        
    Example:
        GET /api/plugins
        GET /api/plugins?source=official
        GET /api/plugins?category=Substitution
        GET /api/plugins?enabled=true
    """
    try:
        manager = get_plugin_manager()
        
        # Récupérer les paramètres de filtre
        source = request.args.get('source')
        category = request.args.get('category')
        enabled_param = request.args.get('enabled')
        
        # Convertir enabled en booléen
        enabled_only = True  # Par défaut
        if enabled_param is not None:
            enabled_only = enabled_param.lower() in ['true', '1', 'yes']
        
        # Lister les plugins avec filtres
        plugins = manager.list_plugins(
            source=source,
            category=category,
            enabled_only=enabled_only
        )
        
        logger.info(
            f"Liste plugins : {len(plugins)} résultats "
            f"(source={source}, category={category}, enabled={enabled_only})"
        )
        
        return jsonify({
            "plugins": plugins,
            "total": len(plugins),
            "filters": {
                "source": source,
                "category": category,
                "enabled": enabled_only
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors du listage des plugins: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors du listage des plugins",
            "message": str(e)
        }), 500


@bp.route('/<plugin_name>', methods=['GET'])
def get_plugin_info(plugin_name: str):
    """
    Récupère les informations détaillées d'un plugin.
    
    Args:
        plugin_name (str): Nom du plugin
        
    Returns:
        JSON: Informations complètes du plugin incluant metadata
        
    Example:
        GET /api/plugins/caesar
    """
    try:
        manager = get_plugin_manager()
        
        plugin_info = manager.get_plugin_info(plugin_name)
        
        if not plugin_info:
            logger.warning(f"Plugin non trouvé: {plugin_name}")
            return jsonify({
                "error": "Plugin non trouvé",
                "plugin_name": plugin_name
            }), 404
        
        logger.info(f"Informations récupérées pour plugin: {plugin_name}")
        
        return jsonify(plugin_info), 200
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la récupération du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de la récupération des informations",
            "message": str(e)
        }), 500


@bp.route('/<plugin_name>/interface', methods=['GET'])
def get_plugin_interface(plugin_name: str):
    """
    Génère l'interface HTML du formulaire pour un plugin.
    
    L'interface est générée dynamiquement à partir des input_types
    définis dans le plugin.json.
    
    Args:
        plugin_name (str): Nom du plugin
        
    Returns:
        HTML: Formulaire d'interface du plugin
        
    Example:
        GET /api/plugins/caesar/interface
    """
    try:
        manager = get_plugin_manager()
        
        plugin_info = manager.get_plugin_info(plugin_name)
        
        if not plugin_info:
            return jsonify({
                "error": "Plugin non trouvé",
                "plugin_name": plugin_name
            }), 404
        
        # Générer l'interface HTML
        html = _generate_plugin_interface_html(plugin_info)
        
        logger.info(f"Interface générée pour plugin: {plugin_name}")
        
        return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la génération de l'interface pour {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de la génération de l'interface",
            "message": str(e)
        }), 500


# =============================================================================
# Routes d'exécution
# =============================================================================

@bp.route('/<plugin_name>/execute', methods=['POST'])
def execute_plugin(plugin_name: str):
    """
    Exécute un plugin de manière synchrone.
    
    Cette route est adaptée pour les plugins rapides (< 1s).
    Pour les plugins longs, utiliser /api/tasks (Phase 2.2).
    
    Args:
        plugin_name (str): Nom du plugin à exécuter
        
    Request Body (JSON):
        inputs (dict): Paramètres d'entrée du plugin
        
    Returns:
        JSON: Résultat de l'exécution au format standardisé
        
    Example:
        POST /api/plugins/caesar/execute
        {
            "inputs": {
                "text": "HELLO",
                "mode": "encode",
                "shift": 13
            }
        }
    """
    try:
        manager = get_plugin_manager()
        
        # Récupérer les inputs depuis le body JSON (gestion explicite des erreurs JSON)
        try:
            data = request.get_json(force=True)
        except Exception as json_error:
            return jsonify({
                "error": "JSON invalide",
                "message": f"Le body de la requête doit être un JSON valide: {str(json_error)}"
            }), 400
        
        if not data or 'inputs' not in data:
            return jsonify({
                "error": "Requête invalide",
                "message": "Le champ 'inputs' est requis dans le body JSON"
            }), 400
        
        inputs = data['inputs']
        
        logger.info(
            f"Exécution synchrone du plugin {plugin_name} "
            f"avec inputs: {list(inputs.keys())}"
        )
        
        # Exécuter le plugin
        result = manager.execute_plugin(plugin_name, inputs)
        
        if not result:
            return jsonify({
                "error": "Erreur d'exécution",
                "message": f"Le plugin {plugin_name} n'a pas pu être exécuté"
            }), 500
        
        logger.info(
            f"Plugin {plugin_name} exécuté avec succès "
            f"(status: {result.get('status')})"
        )
        
        return jsonify(result), 200
        
    except Exception as e:
        logger.error(
            f"Erreur lors de l'exécution du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur d'exécution",
            "message": str(e)
        }), 500


# =============================================================================
# Routes de gestion
# =============================================================================

@bp.route('/discover', methods=['POST'])
def discover_plugins():
    """
    Redéclenche la découverte des plugins.
    
    Scanne les répertoires plugins/official/ et plugins/custom/
    pour découvrir les nouveaux plugins ou détecter les modifications.
    
    Returns:
        JSON: {
            "discovered": nombre de plugins découverts,
            "plugins": liste des plugins,
            "errors": erreurs éventuelles
        }
        
    Example:
        POST /api/plugins/discover
    """
    try:
        manager = get_plugin_manager()
        
        logger.info("Déclenchement de la découverte de plugins")
        
        # Lancer la découverte
        discovered = manager.discover_plugins()
        
        # Récupérer les erreurs
        errors = manager.get_discovery_errors()
        
        logger.info(
            f"Découverte terminée: {len(discovered)} plugins, "
            f"{len(errors)} erreurs"
        )
        
        return jsonify({
            "discovered": len(discovered),
            "plugins": discovered,
            "errors": errors,
            "message": f"{len(discovered)} plugin(s) découvert(s)"
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors de la découverte: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la découverte",
            "message": str(e)
        }), 500


@bp.route('/status', methods=['GET'])
def get_plugins_status():
    """
    Récupère le statut de tous les plugins (enabled, loaded, errors).
    
    Returns:
        JSON: {
            "plugins": {
                "plugin_name": {
                    "enabled": bool,
                    "loaded": bool,
                    "error": str or null,
                    ...
                }
            }
        }
        
    Example:
        GET /api/plugins/status
    """
    try:
        manager = get_plugin_manager()
        
        status = manager.get_plugin_status()
        
        logger.info(f"Statut récupéré pour {len(status)} plugins")
        
        return jsonify({
            "plugins": status,
            "total": len(status),
            "loaded": sum(1 for p in status.values() if p['loaded']),
            "enabled": sum(1 for p in status.values() if p['enabled'])
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du statut: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la récupération du statut",
            "message": str(e)
        }), 500


@bp.route('/<plugin_name>/reload', methods=['POST'])
def reload_plugin(plugin_name: str):
    """
    Recharge un plugin (décharge puis recharge).
    
    Utile après modification du code du plugin.
    
    Args:
        plugin_name (str): Nom du plugin à recharger
        
    Returns:
        JSON: {
            "success": bool,
            "message": str
        }
        
    Example:
        POST /api/plugins/caesar/reload
    """
    try:
        manager = get_plugin_manager()
        
        logger.info(f"Rechargement du plugin: {plugin_name}")
        
        success = manager.reload_plugin(plugin_name)
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Plugin {plugin_name} rechargé avec succès"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Échec du rechargement du plugin {plugin_name}"
            }), 500
            
    except Exception as e:
        logger.error(
            f"Erreur lors du rechargement du plugin {plugin_name}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors du rechargement",
            "message": str(e)
        }), 500


# =============================================================================
# Utilitaires de génération HTML
# =============================================================================

def _generate_plugin_interface_html(plugin_info: Dict[str, Any]) -> str:
    """
    Génère l'interface HTML d'un plugin à partir de ses métadonnées.
    
    Args:
        plugin_info (dict): Informations du plugin incluant metadata
        
    Returns:
        str: Code HTML du formulaire
    """
    metadata = plugin_info.get('metadata', {})
    input_types = metadata.get('input_types', {})
    
    # Template HTML de base
    template = '''
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ plugin_name }} - Interface</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #1e1e1e;
            color: #d4d4d4;
            padding: 20px;
            margin: 0;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        .header {
            background-color: #252526;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
        }
        h1 {
            margin: 0 0 10px 0;
            color: #569cd6;
        }
        .description {
            color: #858585;
            margin: 10px 0;
        }
        .categories {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }
        .category-badge {
            background-color: #3e3e42;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            color: #cccccc;
        }
        .form-container {
            background-color: #252526;
            padding: 20px;
            border-radius: 8px;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #cccccc;
            font-weight: 500;
        }
        input[type="text"],
        input[type="number"],
        textarea,
        select {
            width: 100%;
            padding: 10px;
            background-color: #3c3c3c;
            border: 1px solid #555555;
            border-radius: 4px;
            color: #d4d4d4;
            font-size: 14px;
            box-sizing: border-box;
        }
        input[type="text"]:focus,
        input[type="number"]:focus,
        textarea:focus,
        select:focus {
            outline: none;
            border-color: #007acc;
        }
        input[type="checkbox"] {
            width: 18px;
            height: 18px;
            margin-right: 8px;
        }
        .checkbox-group {
            display: flex;
            align-items: center;
        }
        .button-group {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        button {
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            font-size: 14px;
            cursor: pointer;
            font-weight: 500;
        }
        .btn-primary {
            background-color: #007acc;
            color: white;
        }
        .btn-primary:hover {
            background-color: #005a9e;
        }
        .btn-secondary {
            background-color: #3e3e42;
            color: #cccccc;
        }
        .btn-secondary:hover {
            background-color: #505050;
        }
        .placeholder {
            color: #858585;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ plugin_name }}</h1>
            <div class="description">{{ description }}</div>
            <div class="description" style="font-size: 12px;">Version {{ version }} - {{ author }}</div>
            <div class="categories">
                {% for category in categories %}
                <span class="category-badge">{{ category }}</span>
                {% endfor %}
            </div>
        </div>
        
        <div class="form-container">
            <form id="plugin-form">
                {% for input_name, input_config in input_types.items() %}
                <div class="form-group">
                    <label for="{{ input_name }}">{{ input_config.label }}</label>
                    
                    {% if input_config.type == "string" or input_config.type == "textarea" %}
                        {% if input_config.type == "textarea" %}
                        <textarea 
                            id="{{ input_name }}" 
                            name="{{ input_name }}"
                            placeholder="{{ input_config.placeholder or '' }}"
                            rows="4">{{ input_config.default or '' }}</textarea>
                        {% else %}
                        <input 
                            type="text" 
                            id="{{ input_name }}" 
                            name="{{ input_name }}"
                            placeholder="{{ input_config.placeholder or '' }}"
                            value="{{ input_config.default or '' }}">
                        {% endif %}
                    
                    {% elif input_config.type == "number" or input_config.type == "float" %}
                        <input 
                            type="number" 
                            id="{{ input_name }}" 
                            name="{{ input_name }}"
                            value="{{ input_config.default or 0 }}"
                            min="{{ input_config.min or '' }}"
                            max="{{ input_config.max or '' }}"
                            step="{{ input_config.step or 'any' }}">
                    
                    {% elif input_config.type == "select" %}
                        <select id="{{ input_name }}" name="{{ input_name }}">
                            {% for option in input_config.options %}
                                {% if option is mapping %}
                                <option value="{{ option.value }}" {% if option.value == input_config.default %}selected{% endif %}>
                                    {{ option.label }}
                                </option>
                                {% else %}
                                <option value="{{ option }}" {% if option == input_config.default %}selected{% endif %}>
                                    {{ option }}
                                </option>
                                {% endif %}
                            {% endfor %}
                        </select>
                    
                    {% elif input_config.type == "checkbox" or input_config.type == "boolean" %}
                        <div class="checkbox-group">
                            <input 
                                type="checkbox" 
                                id="{{ input_name }}" 
                                name="{{ input_name }}"
                                {% if input_config.default %}checked{% endif %}>
                            <label for="{{ input_name }}" style="margin-bottom: 0;">
                                {{ input_config.description or input_config.label }}
                            </label>
                        </div>
                    {% endif %}
                </div>
                {% endfor %}
                
                <div class="button-group">
                    <button type="submit" class="btn-primary">Exécuter</button>
                    <button type="reset" class="btn-secondary">Réinitialiser</button>
                </div>
            </form>
        </div>
    </div>
    <script>
        // Pré-remplissage automatique des champs à partir des paramètres d'URL (ex: ?text=...)
        document.addEventListener('DOMContentLoaded', function() {
            const urlParams = new URLSearchParams(window.location.search);
            
            // Pour chaque champ du formulaire
            const form = document.getElementById('plugin-form');
            if (form) {
                Array.from(form.elements).forEach(element => {
                    if (element.name && urlParams.has(element.name)) {
                        const paramValue = urlParams.get(element.name);
                        
                        // Gestion spécifique selon le type
                        if (element.type === 'checkbox') {
                            element.checked = paramValue === 'true' || paramValue === '1' || paramValue === 'on';
                        } else {
                            element.value = paramValue;
                        }
                    }
                });
            }
        });
    </script>
</body>
</html>
    '''
    
    return render_template_string(
        template,
        plugin_name=plugin_info.get('name', 'Unknown'),
        version=plugin_info.get('version', '0.0.0'),
        description=plugin_info.get('description', ''),
        author=plugin_info.get('author', 'Unknown'),
        categories=plugin_info.get('categories', []),
        input_types=input_types
    )

@bp.route('/batch-execute', methods=['POST'])
def batch_execute_plugins():
    """
    Exécute un plugin sur plusieurs géocaches en mode batch.
    
    Request body:
    {
        "plugin_name": "caesar",
        "geocache_ids": [123, 456, 789],
        "inputs": {"mode": "decode", "shift": 3},
        "options": {
            "execution_mode": "sequential",  # ou "parallel"
            "max_concurrency": 3,
            "detect_coordinates": true
        }
    }
    
    Response:
    {
        "task_id": "uuid-string",
        "status": "started",
        "total_geocaches": 3,
        "message": "Batch execution started"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400
        
        # Validation des paramètres requis
        plugin_name = data.get('plugin_name')
        geocache_ids = data.get('geocache_ids', [])
        inputs = data.get('inputs', {})
        options = data.get('options', {})
        
        if not plugin_name:
            return jsonify({"error": "plugin_name is required"}), 400
        
        if not geocache_ids or not isinstance(geocache_ids, list):
            return jsonify({"error": "geocache_ids must be a non-empty list"}), 400
        
        # Validation du plugin
        plugin_info = _plugin_manager.get_plugin_info(plugin_name)
        if not plugin_info:
            return jsonify({"error": f"Plugin '{plugin_name}' not found"}), 404
        
        # Options par défaut
        execution_mode = options.get('execution_mode', 'sequential')
        max_concurrency = options.get('max_concurrency', 3)
        detect_coordinates = options.get('detect_coordinates', True)
        
        # Validation du mode d'exécution
        if execution_mode not in ['sequential', 'parallel']:
            return jsonify({"error": "execution_mode must be 'sequential' or 'parallel'"}), 400
        
        # Créer une tâche batch
        task_id = str(uuid.uuid4())
        
        # Récupérer les informations des géocaches
        geocaches = []
        for gc_id in geocache_ids:
            geocache = Geocache.query.get(gc_id)
            if geocache:
                geocaches.append({
                    'id': geocache.id,
                    'gc_code': geocache.gc_code,
                    'name': geocache.name,
                    'description': geocache.description_raw,
                    'hint': geocache.hints,
                    'difficulty': geocache.difficulty,
                    'terrain': geocache.terrain,
                    'coordinates': {
                        'latitude': geocache.latitude,
                        'longitude': geocache.longitude,
                        'coordinates_raw': geocache.coordinates_raw
                    } if geocache.latitude and geocache.longitude else None,
                    'waypoints': geocache.waypoints or []
                })
        
        if len(geocaches) != len(geocache_ids):
            found_ids = [g['id'] for g in geocaches]
            missing_ids = [gid for gid in geocache_ids if gid not in found_ids]
            return jsonify({
                "error": f"Some geocaches not found: {missing_ids}",
                "found_count": len(geocaches),
                "requested_count": len(geocache_ids)
            }), 404
        
        # Démarrer la tâche en arrière-plan
        batch_task = BatchPluginTask(
            task_id=task_id,
            plugin_name=plugin_name,
            geocaches=geocaches,
            inputs=inputs,
            execution_mode=execution_mode,
            max_concurrency=max_concurrency,
            detect_coordinates=detect_coordinates
        )
        
        # Stocker la tâche
        batch_tasks[task_id] = batch_task
        
        # Démarrer l'exécution en arrière-plan
        thread = threading.Thread(target=batch_task.execute)
        thread.daemon = True
        thread.start()
        
        return jsonify({
            "task_id": task_id,
            "status": "started",
            "total_geocaches": len(geocaches),
            "execution_mode": execution_mode,
            "message": f"Batch execution started for {len(geocaches)} geocaches"
        })
        
    except Exception as e:
        current_app.logger.error(f"Error in batch_execute_plugins: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@bp.route('/batch-status/<task_id>', methods=['GET'])
def get_batch_status(task_id):
    """
    Récupère le statut d'une tâche batch.
    
    Response:
    {
        "task_id": "uuid-string",
        "status": "running|completed|failed",
        "progress": {
            "completed": 2,
            "total": 3,
            "percentage": 66.7
        },
        "results": [
            {
                "geocache_id": 123,
                "gc_code": "GC123",
                "status": "completed|error",
                "result": {...},
                "error": "Error message if any",
                "execution_time": 1500,
                "coordinates": {
                    "latitude": 48.123,
                    "longitude": 2.456,
                    "formatted": "N 48° 07.380 E 002° 27.360"
                }
            }
        ],
        "started_at": "2023-...",
        "completed_at": "2023-..."  # si terminé
    }
    """
    if task_id not in batch_tasks:
        return jsonify({"error": "Task not found"}), 404
    
    task = batch_tasks[task_id]
    return jsonify(task.get_status())

@bp.route('/batch-cancel/<task_id>', methods=['POST'])
def cancel_batch_task(task_id):
    """
    Annule une tâche batch en cours.
    
    Response:
    {
        "message": "Task cancelled successfully"
    }
    """
    if task_id not in batch_tasks:
        return jsonify({"error": "Task not found"}), 404
    
    task = batch_tasks[task_id]
    task.cancel()
    
    return jsonify({"message": "Task cancellation requested"})

@bp.route('/batch-list', methods=['GET'])
def list_batch_tasks():
    """
    Liste toutes les tâches batch (actives et terminées).
    
    Response:
    {
        "tasks": [
            {
                "task_id": "uuid",
                "plugin_name": "caesar",
                "status": "completed",
                "total_geocaches": 5,
                "completed_geocaches": 5,
                "started_at": "2023-...",
                "completed_at": "2023-..."
            }
        ]
    }
    """
    tasks_info = []
    for task_id, task in batch_tasks.items():
        tasks_info.append({
            "task_id": task_id,
            "plugin_name": task.plugin_name,
            "status": task.status,
            "total_geocaches": len(task.geocaches),
            "completed_geocaches": len([r for r in task.results if r['status'] == 'completed']),
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None
        })
    
    return jsonify({"tasks": tasks_info})
