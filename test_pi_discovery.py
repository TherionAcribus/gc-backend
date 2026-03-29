#!/usr/bin/env python
"""Test de découverte du plugin Pi Digits"""

from gc_backend import create_app
from gc_backend.blueprints import plugins as plugins_bp

app = create_app()

with app.app_context():
    pm = plugins_bp._plugin_manager
    if not pm:
        print("Erreur: PluginManager non trouvé")
        exit(1)
    
    print("Découverte des plugins...")
    pm.discover_plugins()
    
    errors = pm.get_discovery_errors()
    all_plugins = pm.list_plugins(enabled_only=False)
    
    print(f"\nPlugins découverts: {len(all_plugins)}")
    print(f"Erreurs de découverte: {len(errors)}")
    
    if errors:
        print("\nErreurs:")
        for path, error in errors.items():
            print(f"  - {path}: {error}")
    
    # Chercher le plugin pi_digits
    pi_plugin = pm.get_plugin_info('pi_digits')
    
    if pi_plugin:
        print(f"\n✓ Plugin Pi Digits trouvé!")
        print(f"  Nom: {pi_plugin['name']}")
        print(f"  Version: {pi_plugin['version']}")
        print(f"  Description: {pi_plugin['description']}")
        print(f"  Catégories: {pi_plugin['categories']}")
        if 'metadata' in pi_plugin and 'capabilities' in pi_plugin['metadata']:
            print(f"  Capabilities: {pi_plugin['metadata']['capabilities']}")
        
        # Test d'exécution
        print("\nTest d'exécution du plugin...")
        result = pm.execute_plugin('pi_digits', {
            'text': '49',
            'mode': 'decode',
            'format': 'digits_only'
        })
        
        print(f"  Status: {result['status']}")
        if result['status'] == 'success':
            print(f"  Résultat: {result['results'][0]['text_output']}")
            print(f"  Message: {result['summary']['message']}")
        else:
            print(f"  Erreur: {result.get('summary', {}).get('message', 'Unknown error')}")
    else:
        print("\n✗ Plugin Pi Digits NON trouvé!")
        print("\nPlugins disponibles:")
        for plugin in all_plugins:
            print(f"  - {plugin['name']}")
