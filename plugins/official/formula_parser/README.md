# Plugin Formula Parser

**Version** : 1.0.0  
**Catégorie** : Parser  
**Auteur** : MysterAI

## Description

Le plugin Formula Parser détecte et parse automatiquement les formules de coordonnées GPS contenant des variables dans les descriptions de géocaches Mystery.

## Formats Supportés

### 1. Format Standard
```
N 47° 5E.FTN E 006° 5A.JVF
```
- Lettres variables adjacentes après le point décimal
- Pas d'espaces entre les lettres

### 2. Format avec Espaces
```
N 48° 41.E D B E 006° 09. F C (A / 2)
```
- Lettres séparées par des espaces
- Support des opérations entre parenthèses

### 3. Format avec Opérations
```
N49°18.(B-A)(B-C-F)(D+E) E006°16.(C+F)(D+F)(C+D)
```
- Opérations arithmétiques : `+`, `-`, `*`, `/`
- Parenthèses pour regrouper les opérations
- Peut contenir plusieurs groupes de calculs

### 4. Format avec Variables Multiples
```
N 48° AB.CDE E 006° FG.HIJ
```
- Plusieurs lettres consécutives représentant des variables

### 5. Format avec Degrés/Minutes Fixes + Expressions Parenthésées
```
N49°12.(A/G-238)(I-135)(D/J-1) E005°59.(C-B)(H-K+1)(F-E-135)
```
- Degrés et minutes fixes (49°12. et 005°59.)
- Plusieurs expressions mathématiques entre parenthèses après le point décimal
- Support des opérations arithmétiques complexes avec constantes numériques

## Utilisation

### Via le PluginManager

```python
from gc_backend.plugins.plugin_manager import PluginManager

manager = PluginManager()

# Avec un texte simple
result = manager.execute_plugin("formula_parser", {
    "text": "Les coordonnées sont N 47° 5E.FTN E 006° 5A.JVF"
})

# Avec une description de géocache
geocache = Geocache.query.get(123)
result = manager.execute_plugin("formula_parser", {
    "text": geocache.description
})
```

### Format de Retour

```json
{
  "status": "success",
  "results": [
    {
      "id": "result_1",
      "north": "N 47° 5E.FTN",
      "east": "E 006° 5A.JVF",
      "source": "standard_format",
      "text_output": "N 47° 5E.FTN E 006° 5A.JVF",
      "confidence": 0.9
    }
  ],
  "summary": "1 formule détectée"
}
```

## Champs de Résultat

| Champ | Type | Description |
|-------|------|-------------|
| `id` | string | Identifiant unique du résultat |
| `north` | string | Partie Nord/Sud de la coordonnée |
| `east` | string | Partie Est/Ouest de la coordonnée |
| `source` | string | Type de format détecté |
| `text_output` | string | Formule complète formatée |
| `confidence` | float | Niveau de confiance (0.0 à 1.0) |

## Cas Limites

### Texte sans Formule
```python
result = manager.execute_plugin("formula_parser", {
    "text": "Cette géocache n'a pas de formule"
})
# Retourne : {"status": "success", "results": [], "summary": "Aucune formule détectée"}
```

### Formule Incomplète
Le plugin tente de détecter au moins une partie (Nord ou Est) même si l'autre est manquante.

### Multiples Formules
Si plusieurs formules sont trouvées (rare), elles sont toutes retournées dans `results`.

## Nettoyage Automatique

Le plugin effectue automatiquement :
- Suppression des espaces superflus entre les lettres
- Normalisation des parenthèses et opérateurs
- Gestion des chevauchements entre parties Nord et Est

## Exemples d'Utilisation

### Exemple 1 : Description Simple
```python
text = """
Pour trouver la cache :
Les coordonnées finales sont N 47° 5E.FTN E 006° 5A.JVF
Bonne chance !
"""

result = manager.execute_plugin("formula_parser", {"text": text})
# Détecte : N 47° 5E.FTN et E 006° 5A.JVF
```

### Exemple 2 : Waypoint avec Formule
```python
waypoint_note = "Stage 3: N 48° 41.E D B E 006° 09. F C (A / 2)"
result = manager.execute_plugin("formula_parser", {"text": waypoint_note})
# Détecte et nettoie : N 48° 41.EDB et E 006° 09.FC(A/2)
```

### Exemple 3 : Formule Complexe
```python
text = "N49°18.(B-A)(B-C-F)(D+E) E006°16.(C+F)(D+F)(C+D)"
result = manager.execute_plugin("formula_parser", {"text": text})
# Détecte les opérations complexes entre parenthèses
```

### Exemple 4 : Degrés/Minutes Fixes avec Expressions Parenthésées
```python
text = "N49°12.(A/G-238)(I-135)(D/J-1) E005°59.(C-B)(H-K+1)(F-E-135)"
result = manager.execute_plugin("formula_parser", {"text": text})
# Détecte : degrés/minutes fixes (49°12. et 005°59.) + expressions parenthésées
```

## Intégration avec Formula Solver

Ce plugin est conçu pour être utilisé avec le Formula Solver qui :
1. Détecte les formules avec ce plugin
2. Extrait les questions associées aux variables
3. Permet à l'utilisateur de saisir les réponses
4. Calcule les coordonnées finales

## Patterns Regex Utilisés

### Nord/Sud
- Format simple : `[NS]\s*\d{1,2}\s*°\s*\d{1,2}\.\s*[A-Z]{1,5}`
- Avec opérations : `[NS]\s*\d{1,2}\s*°\s*[A-Z0-9()+*/\-\s]{1,15}\.\s*[A-Z0-9()+*/\-\s]{1,15}`
- Avec espaces : `N\s+\d{1,2}°\s+\d{1,2}\.\s*[A-Z][\s\n]*[A-Z][\s\n]*[A-Z]`
- Degrés/minutes fixes + expressions parenthésées : `[NS]\s*\d{1,2}\s*°\s*\d{1,2}\.\s*(\([A-Z0-9()+*/\-\s]+\)\s*)+`

### Est/Ouest
- Format simple : `[EW]\s*\d{1,3}\s*°\s*\d{1,2}\.\s*[A-Z]{1,5}`
- Avec opérations : `[EW]\s*\d{1,3}\s*°\s*[A-Z0-9()+*/\-\s]{1,15}\.\s*[A-Z0-9()+*/\-\s]{1,15}`
- Avec parenthèses : `E\s+\d{1,3}°\s+\d{1,2}\.\s+[A-Z]\s+[A-Z]\s+\([A-Z]\s*/\s*\d+\)`
- Degrés/minutes fixes + expressions parenthésées : `[EW]\s*\d{1,3}\s*°\s*\d{1,2}\.\s*(\([A-Z0-9()+*/\-\s]+\)\s*)+`

## Tests

Pour exécuter les tests :
```bash
cd gc-backend
pytest plugins/official/formula_parser/tests/
```

## Limitations Connues

1. **Coordonnées numériques pures** : Ne détecte pas les coordonnées sans variables (ex: `N 48° 51.400`)
2. **Formats non-standard** : Certains formats très exotiques peuvent ne pas être détectés
3. **Contexte** : Ne comprend pas le contexte sémantique (résolu par extraction de questions séparée)

## Roadmap

- [ ] Support de formats additionnels (degrés décimaux avec variables)
- [ ] Détection de formules dans les images (OCR)
- [ ] Validation des formules détectées
- [ ] Suggestions de corrections pour formules mal formées

## Auteur et Licence

**Auteur** : MysterAI Team  
**Licence** : Voir LICENSE du projet principal  
**Contact** : Pour signaler des bugs ou proposer des améliorations
