"""
Services pour gc-backend.

Ce module contient les services métier de l'application :
- TaskManager : Gestion des tâches asynchrones
- (Futurs services : ScoringService, CoordinatesService, etc.)
"""

from .task_manager import TaskManager, TaskInfo, TaskStatus
from .written_coordinates_service import WrittenCoordinatesService, WrittenCoordinatesResult

__all__ = ['TaskManager', 'TaskInfo', 'TaskStatus', 'WrittenCoordinatesService', 'WrittenCoordinatesResult']
