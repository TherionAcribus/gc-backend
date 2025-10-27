#!/usr/bin/env python3
"""Test script pour vérifier le système de retry du scraper."""

import logging
import sys
import os

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(__file__))

from gc_backend.geocaches.scraper import GeocachingScraper

# Configuration du logging pour voir les détails
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def test_timeout_error():
    """Test avec une géocache qui timeout (GC40 selon les logs)."""
    scraper = GeocachingScraper()
    try:
        result = scraper.scrape('GC40')  # Celle qui timeout selon les logs
        print(f"SUCCESS: Scraped {result.gc_code}: {result.name}")
    except LookupError as e:
        if str(e) == 'gc_timeout':
            print("SUCCESS: Timeout géré correctement avec LookupError('gc_timeout')")
        else:
            print(f"FAIL: Mauvaise erreur LookupError: {e}")
    except Exception as e:
        print(f"FAIL: Exception inattendue: {type(e).__name__}: {e}")

def test_not_found():
    """Test avec une géocache qui n'existe pas."""
    scraper = GeocachingScraper()
    try:
        result = scraper.scrape('GC999999')  # Code qui n'existe probablement pas
        print(f"FAIL: Devrait avoir levé une exception pour GC999999")
    except LookupError as e:
        if str(e) == 'gc_not_found':
            print("SUCCESS: Géocache non trouvée gérée correctement")
        else:
            print(f"FAIL: Mauvaise erreur LookupError: {e}")
    except Exception as e:
        print(f"FAIL: Exception inattendue: {type(e).__name__}: {e}")

if __name__ == '__main__':
    print("Test du système de retry du scraper...")
    print("=" * 50)

    print("\n1. Test timeout avec retry:")
    test_timeout_error()

    print("\n2. Test géocache non trouvée:")
    test_not_found()

    print("\nTest terminé.")


