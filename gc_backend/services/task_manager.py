"""
Gestionnaire de tâches asynchrones pour l'exécution de plugins.

Le TaskManager permet d'exécuter des plugins de manière asynchrone via
un ThreadPoolExecutor, avec suivi de progression, annulation et nettoyage
automatique.

Utilisation :
    task_manager = TaskManager(max_workers=4)
    task_id = task_manager.submit_task('caesar', {...})
    status = task_manager.get_task_status(task_id)
    task_manager.cancel_task(task_id)
"""

import uuid
import time
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Any, Optional, Callable

from loguru import logger


class TaskStatus(Enum):
    """Statuts possibles d'une tâche."""
    QUEUED = "queued"          # En attente dans la queue
    RUNNING = "running"        # En cours d'exécution
    COMPLETED = "completed"    # Terminée avec succès
    FAILED = "failed"          # Échouée avec erreur
    CANCELLED = "cancelled"    # Annulée par l'utilisateur


@dataclass
class TaskInfo:
    """
    Informations sur une tâche asynchrone.
    
    Attributes:
        task_id (str): Identifiant unique de la tâche
        plugin_name (str): Nom du plugin exécuté
        inputs (dict): Paramètres d'entrée du plugin
        status (TaskStatus): Statut actuel de la tâche
        progress (float): Progression (0-100)
        message (str): Message de progression
        result (dict, optional): Résultat de l'exécution
        error (str, optional): Message d'erreur si échec
        created_at (datetime): Date de création
        started_at (datetime, optional): Date de début d'exécution
        finished_at (datetime, optional): Date de fin
        future (Future, optional): Future de la tâche
        cancel_requested (bool): Indicateur d'annulation demandée
    """
    task_id: str
    plugin_name: str
    inputs: Dict[str, Any]
    status: TaskStatus = TaskStatus.QUEUED
    progress: float = 0.0
    message: str = "Tâche en attente"
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    future: Optional[Future] = None
    cancel_requested: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convertit la TaskInfo en dictionnaire pour l'API.
        
        Returns:
            dict: Représentation sérialisable de la tâche
        """
        return {
            "task_id": self.task_id,
            "plugin_name": self.plugin_name,
            "status": self.status.value,
            "progress": self.progress,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_ms": self._calculate_duration_ms(),
            "cancel_requested": self.cancel_requested
        }
    
    def _calculate_duration_ms(self) -> Optional[float]:
        """Calcule la durée d'exécution en millisecondes."""
        if not self.started_at:
            return None
        
        end_time = self.finished_at or datetime.now()
        duration = (end_time - self.started_at).total_seconds() * 1000
        return round(duration, 2)


class TaskManager:
    """
    Gestionnaire de tâches asynchrones pour l'exécution de plugins.
    
    Utilise un ThreadPoolExecutor pour exécuter les plugins en arrière-plan
    sans bloquer l'application principale.
    
    Attributes:
        max_workers (int): Nombre maximum de workers dans le pool
        executor (ThreadPoolExecutor): Pool de threads
        tasks (dict): Dictionnaire des tâches par task_id
        _lock (Lock): Verrou pour accès thread-safe aux tâches
        _cleanup_thread (Thread): Thread de nettoyage automatique
        _running (bool): Indicateur d'exécution du manager
    """
    
    def __init__(self, max_workers: int = 4, auto_start: bool = True):
        """
        Initialise le TaskManager.
        
        Args:
            max_workers (int): Nombre maximum de workers (défaut: 4)
        """
        self.max_workers = max_workers
        self.executor: Optional[ThreadPoolExecutor] = None
        self.tasks: Dict[str, TaskInfo] = {}
        self._lock = threading.Lock()
        self._running = False
        self._cleanup_thread: Optional[threading.Thread] = None
        self._auto_start = auto_start

        if auto_start:
            self._start_executor()
        else:
            logger.info(
                "TaskManager initialisé en mode différé (max_workers=%s)",
                max_workers
            )

    def _start_executor(self) -> None:
        if self.executor is not None:
            return

        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="plugin_worker"
        )
        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            daemon=True,
            name="task_cleanup"
        )
        self._cleanup_thread.start()

        logger.info(
            "TaskManager prêt (%s workers, auto_start=%s)",
            self.max_workers,
            self._auto_start
        )

    def _ensure_executor(self) -> None:
        if self.executor is None:
            self._start_executor()
    
    def submit_task(
        self,
        plugin_name: str,
        inputs: Dict[str, Any],
        plugin_manager,
        progress_callback: Optional[Callable] = None
    ) -> str:
        """
        Soumet une tâche d'exécution de plugin.
        
        Args:
            plugin_name (str): Nom du plugin à exécuter
            inputs (dict): Paramètres d'entrée du plugin
            plugin_manager: Instance du PluginManager
            progress_callback (callable, optional): Callback pour progression
            
        Returns:
            str: Identifiant unique de la tâche (task_id)
        """
        # Générer un task_id unique
        task_id = str(uuid.uuid4())
        
        # Créer la TaskInfo
        task_info = TaskInfo(
            task_id=task_id,
            plugin_name=plugin_name,
            inputs=inputs
        )
        
        # Stocker la tâche
        with self._lock:
            self.tasks[task_id] = task_info
        
        self._ensure_executor()
        # Soumettre au ThreadPoolExecutor
        future = self.executor.submit(
            self._execute_task,
            task_id,
            plugin_name,
            inputs,
            plugin_manager,
            progress_callback
        )
        
        # Stocker le Future
        task_info.future = future
        
        logger.info(
            f"Tâche {task_id} soumise pour plugin {plugin_name} "
            f"(queue: {self.get_queue_size()})"
        )
        
        return task_id
    
    def _execute_task(
        self,
        task_id: str,
        plugin_name: str,
        inputs: Dict[str, Any],
        plugin_manager,
        progress_callback: Optional[Callable]
    ):
        """
        Exécute une tâche (appelé dans un thread du pool).
        
        Args:
            task_id (str): ID de la tâche
            plugin_name (str): Nom du plugin
            inputs (dict): Paramètres d'entrée
            plugin_manager: Instance du PluginManager
            progress_callback (callable, optional): Callback pour progression
        """
        task_info = self.tasks.get(task_id)
        if not task_info:
            logger.error(f"Tâche {task_id} introuvable")
            return
        
        try:
            # Marquer comme en cours
            task_info.status = TaskStatus.RUNNING
            task_info.started_at = datetime.now()
            task_info.message = "Exécution du plugin..."
            
            logger.info(f"Début exécution tâche {task_id} (plugin: {plugin_name})")
            
            # Vérifier annulation avant de commencer
            if task_info.cancel_requested:
                self._handle_cancellation(task_info)
                return
            
            # Callback de progression interne
            def update_progress(progress: float, message: str = ""):
                if task_info.cancel_requested:
                    raise InterruptedError("Tâche annulée")
                
                task_info.progress = min(100.0, max(0.0, progress))
                if message:
                    task_info.message = message
                
                if progress_callback:
                    progress_callback(task_id, progress, message)
            
            # Mettre à jour progression initiale
            update_progress(0, "Chargement du plugin...")
            
            # Exécuter le plugin
            result = plugin_manager.execute_plugin(plugin_name, inputs)
            
            # Vérifier annulation après exécution
            if task_info.cancel_requested:
                self._handle_cancellation(task_info)
                return
            
            # Marquer comme terminée
            task_info.status = TaskStatus.COMPLETED
            task_info.progress = 100.0
            task_info.message = "Terminée avec succès"
            task_info.result = result
            task_info.finished_at = datetime.now()
            
            logger.info(
                f"Tâche {task_id} terminée avec succès "
                f"(durée: {task_info._calculate_duration_ms()}ms)"
            )
            
        except InterruptedError as e:
            self._handle_cancellation(task_info)
            
        except Exception as e:
            logger.error(
                f"Erreur lors de l'exécution de la tâche {task_id}: {e}",
                exc_info=True
            )
            
            task_info.status = TaskStatus.FAILED
            task_info.error = str(e)
            task_info.message = f"Erreur: {str(e)}"
            task_info.finished_at = datetime.now()
    
    def _handle_cancellation(self, task_info: TaskInfo):
        """Gère l'annulation d'une tâche."""
        task_info.status = TaskStatus.CANCELLED
        task_info.message = "Tâche annulée par l'utilisateur"
        task_info.finished_at = datetime.now()
        
        logger.info(f"Tâche {task_info.task_id} annulée")
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère le statut d'une tâche.
        
        Args:
            task_id (str): Identifiant de la tâche
            
        Returns:
            dict or None: Statut de la tâche ou None si non trouvée
        """
        with self._lock:
            task_info = self.tasks.get(task_id)
            
            if not task_info:
                return None
            
            return task_info.to_dict()
    
    def cancel_task(self, task_id: str) -> bool:
        """
        Demande l'annulation d'une tâche.
        
        L'annulation est "douce" : la tâche doit vérifier périodiquement
        le flag cancel_requested et s'arrêter proprement.
        
        Args:
            task_id (str): Identifiant de la tâche
            
        Returns:
            bool: True si la demande d'annulation a été enregistrée
        """
        with self._lock:
            task_info = self.tasks.get(task_id)
            
            if not task_info:
                logger.warning(f"Tentative d'annulation de tâche inexistante: {task_id}")
                return False
            
            # Si déjà terminée, on ne peut pas annuler
            if task_info.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                logger.warning(
                    f"Impossible d'annuler la tâche {task_id} "
                    f"(statut: {task_info.status.value})"
                )
                return False
            
            # Marquer comme annulation demandée
            task_info.cancel_requested = True
            
            # Si en attente, annuler immédiatement
            if task_info.status == TaskStatus.QUEUED and task_info.future:
                task_info.future.cancel()
                self._handle_cancellation(task_info)
            
            logger.info(f"Annulation demandée pour tâche {task_id}")
            
            return True
    
    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        plugin_name: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Liste les tâches avec filtres optionnels.
        
        Args:
            status (TaskStatus, optional): Filtrer par statut
            plugin_name (str, optional): Filtrer par nom de plugin
            
        Returns:
            list: Liste des tâches correspondant aux critères
        """
        with self._lock:
            tasks = list(self.tasks.values())
        
        # Filtrer par statut
        if status:
            tasks = [t for t in tasks if t.status == status]
        
        # Filtrer par plugin_name
        if plugin_name:
            tasks = [t for t in tasks if t.plugin_name == plugin_name]
        
        # Convertir en dict et trier par date de création (plus récent d'abord)
        return [t.to_dict() for t in sorted(tasks, key=lambda x: x.created_at, reverse=True)]
    
    def cleanup_old_tasks(self, max_age_seconds: int = 3600):
        """
        Nettoie les tâches terminées anciennes.
        
        Args:
            max_age_seconds (int): Age maximum en secondes (défaut: 1h)
        """
        cutoff_time = datetime.now() - timedelta(seconds=max_age_seconds)
        
        with self._lock:
            tasks_to_remove = []
            
            for task_id, task_info in self.tasks.items():
                # Ne nettoyer que les tâches terminées
                if task_info.status not in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                    continue
                
                # Vérifier l'âge
                if task_info.finished_at and task_info.finished_at < cutoff_time:
                    tasks_to_remove.append(task_id)
            
            # Supprimer les tâches
            for task_id in tasks_to_remove:
                del self.tasks[task_id]
            
            if tasks_to_remove:
                logger.info(f"Nettoyage: {len(tasks_to_remove)} tâche(s) supprimée(s)")
    
    def _cleanup_loop(self):
        """
        Boucle de nettoyage automatique (exécutée dans un thread daemon).
        
        Nettoie les vieilles tâches toutes les 5 minutes.
        """
        while self._running:
            time.sleep(300)  # 5 minutes
            
            try:
                self.cleanup_old_tasks(max_age_seconds=3600)  # Conserver 1h
            except Exception as e:
                logger.error(f"Erreur dans le nettoyage automatique: {e}")
    
    def get_queue_size(self) -> int:
        """
        Retourne le nombre de tâches en attente ou en cours.
        
        Returns:
            int: Nombre de tâches actives
        """
        with self._lock:
            return sum(
                1 for t in self.tasks.values()
                if t.status in [TaskStatus.QUEUED, TaskStatus.RUNNING]
            )
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        Retourne des statistiques sur les tâches.
        
        Returns:
            dict: Statistiques (total, par statut, queue size, etc.)
        """
        with self._lock:
            tasks = list(self.tasks.values())
        
        stats = {
            "total": len(tasks),
            "queued": sum(1 for t in tasks if t.status == TaskStatus.QUEUED),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
            "cancelled": sum(1 for t in tasks if t.status == TaskStatus.CANCELLED),
            "max_workers": self.max_workers,
            "active_workers": self.get_queue_size()
        }
        
        return stats
    
    def shutdown(self, wait: bool = True):
        """
        Arrête le TaskManager et libère les ressources.
        
        Args:
            wait (bool): Attendre la fin des tâches en cours (défaut: True)
        """
        logger.info("Arrêt du TaskManager...")
        
        self._running = False
        if self.executor:
            self.executor.shutdown(wait=wait)
            self.executor = None
        
        logger.info("TaskManager arrêté")
    
    def __repr__(self):
        return (
            f"<TaskManager workers={self.max_workers} "
            f"tasks={len(self.tasks)} queue={self.get_queue_size()}>"
        )
