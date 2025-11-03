"""Script de test pour vérifier les plugins."""

import os
from gc_backend.plugins.plugin_manager import PluginManager

def test_list_plugins():
    """Liste tous les plugins disponibles."""
    plugins_dir = os.path.join(os.path.dirname(__file__), "plugins")
    pm = PluginManager(plugins_dir)
    plugins = pm.list_plugins()
    
    print(f"\n✅ Plugins trouvés: {len(plugins)}\n")
    
    for p in plugins:
        print(f"📦 {p['name']} v{p['version']}")
        print(f"   {p['description'][:80]}")
        print(f"   Catégories: {', '.join(p.get('categories', []))}")
        print()

def test_bacon_code():
    """Test du plugin Bacon Code."""
    plugins_dir = os.path.join(os.path.dirname(__file__), "plugins")
    pm = PluginManager(plugins_dir)
    
    print("\n🧪 Test Bacon Code - Encodage")
    result = pm.execute_plugin("bacon_code", {
        "text": "HELLO",
        "mode": "encode",
        "variant": "26"
    })
    
    print(f"Status: {result['status']}")
    print(f"Résultats: {len(result['results'])}")
    if result['results']:
        print(f"Encodé: {result['results'][0]['text_output']}")
    
    print("\n🧪 Test Bacon Code - Décodage")
    result = pm.execute_plugin("bacon_code", {
        "text": "AABBB AABAA ABABB ABABB ABBBA",
        "mode": "decode",
        "variant": "26"
    })
    
    print(f"Status: {result['status']}")
    if result['results']:
        print(f"Décodé: {result['results'][0]['text_output']}")

def test_fox_code():
    """Test du plugin Fox Code."""
    plugins_dir = os.path.join(os.path.dirname(__file__), "plugins")
    pm = PluginManager(plugins_dir)
    
    print("\n🧪 Test Fox Code - Encodage")
    result = pm.execute_plugin("fox_code", {
        "text": "HELLO",
        "mode": "encode",
        "variant": "long"
    })
    
    print(f"Status: {result['status']}")
    if result['results']:
        print(f"Encodé: {result['results'][0]['text_output']}")
    
    print("\n🧪 Test Fox Code - Décodage")
    result = pm.execute_plugin("fox_code", {
        "text": "18 15 22 22 25",
        "mode": "decode",
        "variant": "long"
    })
    
    print(f"Status: {result['status']}")
    if result['results']:
        print(f"Décodé: {result['results'][0]['text_output']}")

if __name__ == "__main__":
    test_list_plugins()
    test_bacon_code()
    test_fox_code()
    print("\n✅ Tests terminés!\n")
