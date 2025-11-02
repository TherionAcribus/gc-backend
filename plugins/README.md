# Répertoire des Plugins MysterAI

Ce répertoire contient les plugins de chiffrement/déchiffrement pour MysterAI.

## Structure

```
plugins/
├── official/          # Plugins officiels (lecture seule)
│   ├── bacon_code/
│   │   ├── plugin.json
│   │   ├── main.py
│   │   └── README.md
│   ├── caesar/
│   └── ...
└── custom/            # Plugins personnalisés (utilisateur)
    └── (vide initialement)
```

## Plugins Officiels

Les plugins dans `official/` sont fournis avec l'application et sont en lecture seule.
Ils ne doivent pas être modifiés directement.

## Plugins Personnalisés

Les plugins dans `custom/` sont ajoutés par l'utilisateur.
Pour ajouter un plugin personnalisé :

1. Créer un dossier avec le nom du plugin (snake_case)
2. Ajouter un fichier `plugin.json` conforme au schéma
3. Ajouter le point d'entrée (ex: `main.py`)
4. Relancer la découverte des plugins via l'API

## Format plugin.json

Voir le schéma complet dans `gc_backend/plugins/schemas/plugin.schema.json`

### Exemple minimal

```json
{
  "name": "mon_plugin",
  "version": "1.0.0",
  "plugin_api_version": "2.0",
  "description": "Mon plugin de déchiffrement",
  "author": "Moi",
  "plugin_type": "python",
  "entry_point": "main.py",
  "categories": ["Substitution"],
  "input_types": {
    "text": {
      "type": "string",
      "label": "Texte à traiter"
    },
    "mode": {
      "type": "select",
      "label": "Mode",
      "options": ["encode", "decode"],
      "default": "decode"
    }
  }
}
```

## Format de sortie standardisé

Tous les plugins doivent retourner un résultat au format suivant :

```json
{
  "status": "ok|error",
  "summary": "Message résumé",
  "results": [
    {
      "id": "result_1",
      "text_output": "Texte décodé",
      "confidence": 0.85,
      "scoring": {
        "score": 0.85,
        "language": "fr",
        "words_found": ["mot1", "mot2"]
      },
      "coordinates": {
        "exist": true,
        "decimal": {"lat": 49.123, "lon": 2.456},
        "raw": ["N 49° 07.380 E 002° 27.360"]
      },
      "parameters": {
        "mode": "decode",
        "shift": 13
      }
    }
  ],
  "plugin_info": {
    "name": "mon_plugin",
    "version": "1.0.0",
    "execution_time_ms": 42
  }
}
```

## Développement d'un plugin Python

### Structure minimale

```python
class MonPlugin:
    def __init__(self):
        self.name = "mon_plugin"
        self.version = "1.0.0"
    
    def execute(self, inputs: dict) -> dict:
        """
        Point d'entrée principal du plugin.
        
        Args:
            inputs: Dictionnaire contenant les paramètres d'entrée
            
        Returns:
            Dictionnaire au format standardisé
        """
        # Votre logique ici
        
        return {
            "status": "ok",
            "summary": "Traitement réussi",
            "results": [...],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": 0
            }
        }
```

### Méthodes optionnelles

- `check_code(text, strict, allowed_chars, embedded)` : Pour mode détection
- `encode(text)` : Pour l'encodage
- `decode(text)` : Pour le décodage

## Catégories disponibles

- `Substitution` : Chiffrement par substitution
- `Transposition` : Chiffrement par transposition
- `Coordinates` : Conversion/analyse de coordonnées
- `Solver` : Résolution d'énigmes
- `AlphabetsDecryption` : Décryptage alphabétique
- (Voir `plugin_categories.json` pour la liste complète)

## Sécurité

Les plugins peuvent déclarer leurs besoins :
- `heavy_cpu: true` : Plugin CPU intensif (exécuté en ProcessPool)
- `needs_network: true` : Nécessite accès réseau
- `needs_filesystem: true` : Nécessite accès au système de fichiers

Ces déclarations permettent au système d'appliquer les bonnes politiques de sécurité.
