"""
Tests pour les endpoints API des tâches asynchrones.

Ces tests vérifient :
- La création de tâches
- Le suivi du statut
- L'annulation
- Le listage et les statistiques
"""

import pytest
import json
import time
from pathlib import Path

from gc_backend import create_app
from gc_backend.database import db


@pytest.fixture
def app():
    """Crée une instance de l'application pour les tests."""
    app = create_app()
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    """Crée un client de test."""
    return app.test_client()


class TestCreateTask:
    """Tests de création de tâches."""
    
    def test_create_task_success(self, client):
        """Test création d'une tâche avec succès."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        response = client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {
                    'text': 'HELLO',
                    'mode': 'encode',
                    'shift': 13
                }
            }),
            content_type='application/json'
        )
        
        assert response.status_code == 201
        data = json.loads(response.data)
        
        assert 'task_id' in data
        assert data['status'] == 'queued'
        assert 'message' in data
    
    def test_create_task_missing_plugin_name(self, client):
        """Test création sans plugin_name."""
        response = client.post(
            '/api/tasks',
            data=json.dumps({
                'inputs': {'text': 'TEST'}
            }),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        
        assert 'error' in data
        assert 'plugin_name' in data['message']
    
    def test_create_task_missing_inputs(self, client):
        """Test création sans inputs."""
        response = client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar'
            }),
            content_type='application/json'
        )
        
        assert response.status_code == 400
        data = json.loads(response.data)
        
        assert 'error' in data
        assert 'inputs' in data['message']
    
    def test_create_task_invalid_json(self, client):
        """Test création avec JSON invalide."""
        response = client.post(
            '/api/tasks',
            data='invalid json',
            content_type='application/json'
        )
        
        assert response.status_code in [400, 415]


class TestGetTaskStatus:
    """Tests de récupération du statut."""
    
    def test_get_task_status_success(self, client):
        """Test récupération du statut d'une tâche existante."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer une tâche
        create_response = client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {'text': 'ABC', 'mode': 'encode', 'shift': 1}
            }),
            content_type='application/json'
        )
        
        task_id = json.loads(create_response.data)['task_id']
        
        # Récupérer le statut
        response = client.get(f'/api/tasks/{task_id}')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['task_id'] == task_id
        assert 'status' in data
        assert 'progress' in data
        assert 'plugin_name' in data
    
    def test_get_task_status_not_found(self, client):
        """Test récupération d'une tâche inexistante."""
        response = client.get('/api/tasks/nonexistent-task-id')
        
        assert response.status_code == 404
        data = json.loads(response.data)
        
        assert 'error' in data
    
    def test_get_task_status_completed(self, client):
        """Test que le statut passe à completed."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer une tâche rapide
        create_response = client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {'text': 'ABC', 'mode': 'encode', 'shift': 1}
            }),
            content_type='application/json'
        )
        
        task_id = json.loads(create_response.data)['task_id']
        
        # Attendre la fin
        time.sleep(2)
        
        # Vérifier le statut
        response = client.get(f'/api/tasks/{task_id}')
        data = json.loads(response.data)
        
        assert data['status'] == 'completed'
        assert data['progress'] == 100.0
        assert data['result'] is not None


class TestCancelTask:
    """Tests d'annulation de tâches."""
    
    def test_cancel_task_success(self, client):
        """Test annulation d'une tâche."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer une tâche longue
        create_response = client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {
                    'text': 'HELLO' * 100,
                    'mode': 'decode',
                    'brute_force': True
                }
            }),
            content_type='application/json'
        )
        
        task_id = json.loads(create_response.data)['task_id']
        
        # Annuler immédiatement
        response = client.post(f'/api/tasks/{task_id}/cancel')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert data['success'] is True
        assert 'Annulation' in data['message']
    
    def test_cancel_completed_task(self, client):
        """Test qu'on ne peut pas annuler une tâche terminée."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer une tâche rapide
        create_response = client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {'text': 'ABC', 'mode': 'encode', 'shift': 1}
            }),
            content_type='application/json'
        )
        
        task_id = json.loads(create_response.data)['task_id']
        
        # Attendre la fin
        time.sleep(2)
        
        # Tenter d'annuler
        response = client.post(f'/api/tasks/{task_id}/cancel')
        
        assert response.status_code == 400
        data = json.loads(response.data)
        
        assert data['success'] is False


class TestListTasks:
    """Tests de listage des tâches."""
    
    def test_list_all_tasks(self, client):
        """Test listage de toutes les tâches."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer quelques tâches
        for i in range(3):
            client.post(
                '/api/tasks',
                data=json.dumps({
                    'plugin_name': 'caesar',
                    'inputs': {'text': f'TEST{i}', 'mode': 'encode', 'shift': 1}
                }),
                content_type='application/json'
            )
        
        # Lister
        response = client.get('/api/tasks')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'tasks' in data
        assert 'total' in data
        assert data['total'] >= 3
    
    def test_list_tasks_by_status(self, client):
        """Test filtrage par statut."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer une tâche
        client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {'text': 'ABC', 'mode': 'encode', 'shift': 1}
            }),
            content_type='application/json'
        )
        
        # Attendre qu'elle se termine
        time.sleep(2)
        
        # Filtrer par statut completed
        response = client.get('/api/tasks?status=completed')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'tasks' in data
        # Toutes les tâches doivent être completed
        for task in data['tasks']:
            assert task['status'] == 'completed'
    
    def test_list_tasks_invalid_status(self, client):
        """Test avec statut invalide."""
        response = client.get('/api/tasks?status=invalid_status')
        
        assert response.status_code == 400
        data = json.loads(response.data)
        
        assert 'error' in data
        assert 'valid_statuses' in data


class TestTaskStatistics:
    """Tests des statistiques."""
    
    def test_get_statistics(self, client):
        """Test récupération des statistiques."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer quelques tâches
        for i in range(3):
            client.post(
                '/api/tasks',
                data=json.dumps({
                    'plugin_name': 'caesar',
                    'inputs': {'text': 'TEST', 'mode': 'encode', 'shift': 1}
                }),
                content_type='application/json'
            )
        
        # Récupérer stats
        response = client.get('/api/tasks/statistics')
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'total' in data
        assert 'queued' in data
        assert 'running' in data
        assert 'completed' in data
        assert 'max_workers' in data
        
        assert data['total'] >= 3


class TestCleanupTasks:
    """Tests du nettoyage."""
    
    def test_cleanup_old_tasks(self, client):
        """Test nettoyage des vieilles tâches."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer une tâche
        client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {'text': 'ABC', 'mode': 'encode', 'shift': 1}
            }),
            content_type='application/json'
        )
        
        # Attendre qu'elle se termine
        time.sleep(2)
        
        # Nettoyer avec max_age très court
        response = client.post(
            '/api/tasks/cleanup',
            data=json.dumps({'max_age_seconds': 0}),
            content_type='application/json'
        )
        
        assert response.status_code == 200
        data = json.loads(response.data)
        
        assert 'message' in data
        assert 'tasks_before' in data
        assert 'tasks_after' in data


class TestTaskAPIIntegration:
    """Tests d'intégration complets."""
    
    def test_full_async_workflow(self, client, app):
        """Test workflow complet : create → status → wait → result."""
        plugins_dir = Path(__file__).parent.parent / 'plugins'
        if not (plugins_dir / 'official' / 'caesar').exists():
            pytest.skip("Plugin Caesar non disponible")
        
        # Créer le plugin Caesar en DB (nécessaire car TESTING=1 désactive la découverte auto)
        from gc_backend.plugins.models import Plugin
        from gc_backend.database import db
        
        with app.app_context():
            caesar = Plugin(
                name='caesar',
                version='1.0.0',
                plugin_api_version='2.0',
                description='Caesar cipher plugin',
                author='MysterAI',
                plugin_type='python',
                source='official',
                path=str(plugins_dir / 'official' / 'caesar'),
                entry_point='main.py',
                enabled=True
            )
            db.session.add(caesar)
            db.session.commit()
            
            # Forcer le chargement du plugin dans le PluginManager
            app.plugin_manager.discover_plugins()
        
        # 1. Créer une tâche
        create_response = client.post(
            '/api/tasks',
            data=json.dumps({
                'plugin_name': 'caesar',
                'inputs': {
                    'text': 'HELLO',
                    'mode': 'encode',
                    'shift': 13
                }
            }),
            content_type='application/json'
        )
        
        assert create_response.status_code == 201
        task_id = json.loads(create_response.data)['task_id']
        
        # 2. Vérifier le statut initial (queued ou running)
        status_response = client.get(f'/api/tasks/{task_id}')
        status_data = json.loads(status_response.data)
        assert status_data['status'] in ['queued', 'running', 'completed']
        
        # 3. Attendre la fin
        time.sleep(2)
        
        # 4. Vérifier le résultat
        final_response = client.get(f'/api/tasks/{task_id}')
        final_data = json.loads(final_response.data)
        
        assert final_data['status'] == 'completed'
        assert final_data['result'] is not None
        assert final_data['result']['status'] == 'ok'
        assert final_data['result']['results'][0]['text_output'] == 'URYYB'
