"""
Configuration globale pytest pour les tests gc-backend.

Ce fichier configure l'environnement de test :
- Désactive la découverte automatique des plugins pendant les tests
- Configure les fixtures globales
"""
import os
import pytest


@pytest.fixture(scope='session', autouse=True)
def disable_plugin_discovery():
    """
    Désactive la découverte automatique des plugins pendant les tests.
    
    Les tests créent leurs propres fixtures de plugins et ne doivent pas
    être pollués par les plugins réels du système.
    """
    os.environ['TESTING'] = '1'
    yield
    del os.environ['TESTING']
