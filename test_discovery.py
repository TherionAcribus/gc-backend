"""Test de découverte du plugin formula_parser"""
import os
from pathlib import Path
from gc_backend.plugins.plugin_manager import PluginManager

# Chemin vers le dossier plugins
plugins_dir = Path(__file__).parent / "plugins"

pm = PluginManager(plugins_dir=str(plugins_dir))

# Force la découverte
print("Lancement de la découverte des plugins...")
plugins = pm.discover_plugins()  # Retourne la liste des plugins découverts

print("\n=== PLUGINS DÉCOUVERTS ===")
for p in plugins:
    print(f"✓ {p['name']} v{p['version']} ({p.get('source', 'unknown')})")

print(f"\nTotal: {len(plugins)} plugins\n")

# Test spécifique du formula_parser
if any(p['name'] == 'formula_parser' for p in plugins):
    print("✅ SUCCESS: formula_parser a été découvert!")
    
    # Test d'exécution
    print("\n=== TEST D'EXÉCUTION ===")
    result = pm.execute_plugin("formula_parser", {
        "text": "Les coordonnées finales sont N 47° 5E.FTN E 006° 5A.JVF"
    })
    print(f"Status: {result.get('status')}")
    print(f"Summary: {result.get('summary')}")
    if result.get('results'):
        print(f"Résultats: {len(result['results'])} formule(s) détectée(s)")
        for r in result['results']:
            print(f"  - {r.get('text_output')}")
else:
    print("❌ ERREUR: formula_parser n'a PAS été découvert")
    print("\nPlugins disponibles:")
    for p in plugins:
        print(f"  - {p['name']}")
