"""
Blueprint pour les endpoints API des tâches asynchrones.

Ce module expose les routes REST pour :
- Créer une tâche d'exécution de plugin (asynchrone)
- Récupérer le statut d'une tâche
- Annuler une tâche
- Lister toutes les tâches
- Statistiques sur les tâches
"""

from flask import Blueprint, jsonify, request
from loguru import logger

from ..services import TaskManager, TaskStatus


# Créer le blueprint
bp = Blueprint('tasks', __name__, url_prefix='/api/tasks')

# Instance globale du TaskManager (sera initialisée dans create_app)
_task_manager: TaskManager = None
_plugin_manager = None


def init_task_manager(task_manager: TaskManager, plugin_manager):
    """
    Initialise le TaskManager et PluginManager globaux pour ce blueprint.
    
    Args:
        task_manager (TaskManager): Instance du gestionnaire de tâches
        plugin_manager: Instance du gestionnaire de plugins
    """
    global _task_manager, _plugin_manager
    _task_manager = task_manager
    _plugin_manager = plugin_manager
    logger.info("TaskManager initialisé dans le blueprint tasks")


def get_task_manager() -> TaskManager:
    """Récupère l'instance du TaskManager."""
    if _task_manager is None:
        raise RuntimeError("TaskManager non initialisé")
    return _task_manager


def get_plugin_manager():
    """Récupère l'instance du PluginManager."""
    if _plugin_manager is None:
        raise RuntimeError("PluginManager non initialisé")
    return _plugin_manager


# =============================================================================
# Routes de gestion des tâches
# =============================================================================

@bp.route('', methods=['POST'])
def create_task():
    """
    Crée et démarre une nouvelle tâche asynchrone.
    
    Request Body (JSON):
        plugin_name (str): Nom du plugin à exécuter
        inputs (dict): Paramètres d'entrée du plugin
        
    Returns:
        JSON: {
            "task_id": str,
            "status": str,
            "message": str
        }
        
    Example:
        POST /api/tasks
        {
            "plugin_name": "caesar",
            "inputs": {
                "text": "HELLO",
                "mode": "decode",
                "brute_force": true
            }
        }
    """
    try:
        task_manager = get_task_manager()
        plugin_manager = get_plugin_manager()
        
        # Récupérer les données (gestion explicite des erreurs JSON)
        try:
            data = request.get_json(force=True)
        except Exception as json_error:
            return jsonify({
                "error": "JSON invalide",
                "message": f"Le body de la requête doit être un JSON valide: {str(json_error)}"
            }), 400
        
        if not data:
            return jsonify({
                "error": "Requête invalide",
                "message": "Body JSON requis"
            }), 400
        
        plugin_name = data.get('plugin_name')
        inputs = data.get('inputs')
        
        if not plugin_name:
            return jsonify({
                "error": "Paramètre manquant",
                "message": "Le champ 'plugin_name' est requis"
            }), 400
        
        if not inputs or not isinstance(inputs, dict):
            return jsonify({
                "error": "Paramètre invalide",
                "message": "Le champ 'inputs' doit être un dictionnaire"
            }), 400
        
        # Soumettre la tâche
        task_id = task_manager.submit_task(
            plugin_name=plugin_name,
            inputs=inputs,
            plugin_manager=plugin_manager
        )
        
        logger.info(
            f"Tâche {task_id} créée pour plugin {plugin_name}"
        )
        
        return jsonify({
            "task_id": task_id,
            "status": "queued",
            "message": f"Tâche créée et soumise pour exécution"
        }), 201
        
    except Exception as e:
        logger.error(f"Erreur lors de la création de la tâche: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la création de la tâche",
            "message": str(e)
        }), 500


@bp.route('/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    """
    Récupère le statut d'une tâche.
    
    Args:
        task_id (str): Identifiant de la tâche
        
    Returns:
        JSON: Statut complet de la tâche
        
    Example:
        GET /api/tasks/550e8400-e29b-41d4-a716-446655440000
    """
    try:
        task_manager = get_task_manager()
        
        status = task_manager.get_task_status(task_id)
        
        if not status:
            return jsonify({
                "error": "Tâche non trouvée",
                "task_id": task_id
            }), 404
        
        return jsonify(status), 200
        
    except Exception as e:
        logger.error(
            f"Erreur lors de la récupération du statut de la tâche {task_id}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de la récupération du statut",
            "message": str(e)
        }), 500


@bp.route('/<task_id>/cancel', methods=['POST'])
def cancel_task(task_id: str):
    """
    Demande l'annulation d'une tâche.
    
    L'annulation est "douce" : la tâche doit vérifier périodiquement
    le flag et s'arrêter proprement.
    
    Args:
        task_id (str): Identifiant de la tâche
        
    Returns:
        JSON: {
            "success": bool,
            "message": str
        }
        
    Example:
        POST /api/tasks/550e8400-e29b-41d4-a716-446655440000/cancel
    """
    try:
        task_manager = get_task_manager()
        
        success = task_manager.cancel_task(task_id)
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Annulation demandée pour la tâche {task_id}"
            }), 200
        else:
            return jsonify({
                "success": False,
                "message": f"Impossible d'annuler la tâche {task_id} (déjà terminée ou inexistante)"
            }), 400
            
    except Exception as e:
        logger.error(
            f"Erreur lors de l'annulation de la tâche {task_id}: {e}",
            exc_info=True
        )
        return jsonify({
            "error": "Erreur lors de l'annulation",
            "message": str(e)
        }), 500


@bp.route('', methods=['GET'])
def list_tasks():
    """
    Liste toutes les tâches avec filtres optionnels.
    
    Query Parameters:
        status (str, optional): Filtrer par statut
        plugin_name (str, optional): Filtrer par nom de plugin
        
    Returns:
        JSON: {
            "tasks": [liste des tâches],
            "total": nombre total
        }
        
    Example:
        GET /api/tasks
        GET /api/tasks?status=running
        GET /api/tasks?plugin_name=caesar
    """
    try:
        task_manager = get_task_manager()
        
        # Récupérer les filtres
        status_str = request.args.get('status')
        plugin_name = request.args.get('plugin_name')
        
        # Convertir le statut
        status = None
        if status_str:
            try:
                status = TaskStatus(status_str.lower())
            except ValueError:
                return jsonify({
                    "error": "Statut invalide",
                    "message": f"Statut '{status_str}' non reconnu",
                    "valid_statuses": [s.value for s in TaskStatus]
                }), 400
        
        # Lister les tâches
        tasks = task_manager.list_tasks(
            status=status,
            plugin_name=plugin_name
        )
        
        return jsonify({
            "tasks": tasks,
            "total": len(tasks),
            "filters": {
                "status": status_str,
                "plugin_name": plugin_name
            }
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors du listage des tâches: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors du listage des tâches",
            "message": str(e)
        }), 500


@bp.route('/statistics', methods=['GET'])
def get_statistics():
    """
    Récupère les statistiques sur les tâches.
    
    Returns:
        JSON: Statistiques (total, par statut, workers, etc.)
        
    Example:
        GET /api/tasks/statistics
    """
    try:
        task_manager = get_task_manager()
        
        stats = task_manager.get_statistics()
        
        return jsonify(stats), 200
        
    except Exception as e:
        logger.error(f"Erreur lors de la récupération des statistiques: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors de la récupération des statistiques",
            "message": str(e)
        }), 500


@bp.route('/cleanup', methods=['POST'])
def cleanup_old_tasks():
    """
    Nettoie les tâches terminées anciennes.
    
    Request Body (JSON, optional):
        max_age_seconds (int): Age maximum en secondes (défaut: 3600)
        
    Returns:
        JSON: {
            "message": str,
            "tasks_before": int,
            "tasks_after": int
        }
        
    Example:
        POST /api/tasks/cleanup
        {"max_age_seconds": 1800}
    """
    try:
        task_manager = get_task_manager()
        
        # Compter avant nettoyage
        stats_before = task_manager.get_statistics()
        tasks_before = stats_before['total']
        
        # Récupérer le max_age
        data = request.get_json() or {}
        max_age_seconds = data.get('max_age_seconds', 3600)
        
        # Nettoyer
        task_manager.cleanup_old_tasks(max_age_seconds=max_age_seconds)
        
        # Compter après
        stats_after = task_manager.get_statistics()
        tasks_after = stats_after['total']
        
        removed = tasks_before - tasks_after
        
        return jsonify({
            "message": f"{removed} tâche(s) nettoyée(s)",
            "tasks_before": tasks_before,
            "tasks_after": tasks_after,
            "max_age_seconds": max_age_seconds
        }), 200
        
    except Exception as e:
        logger.error(f"Erreur lors du nettoyage: {e}", exc_info=True)
        return jsonify({
            "error": "Erreur lors du nettoyage",
            "message": str(e)
        }), 500
