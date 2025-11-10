# Formula Questions Service

Service d'extraction automatique de questions associées aux variables dans les formules de coordonnées GPS.

## Vue d'ensemble

Le `FormulaQuestionsService` analyse les descriptions de géocaches Mystery pour extraire les questions qui correspondent aux variables (lettres) dans les formules de coordonnées.

Par exemple, pour une formule `N 47° 5E.FTN`, le service peut extraire :
- **E** : "Nombre de fenêtres sur la façade"
- **F** : "Année de construction - 1900"
- **T** : "Nombre de marches jusqu'à la porte"
- **N** : "Numéro de la rue"

## Installation

Le service est automatiquement disponible via l'instance singleton :

```python
from gc_backend.services.formula_questions_service import formula_questions_service
```

## Utilisation

### Extraction basique (texte brut)

```python
from gc_backend.services.formula_questions_service import formula_questions_service

text = """
Pour résoudre cette énigme:
A. Combien de fenêtres sur la façade?
B. Année de construction du bâtiment?
C. Numéro de la rue?
"""

# Extraire les questions pour les lettres A, B, C
result = formula_questions_service.extract_questions_with_regex(text, ['A', 'B', 'C'])

print(result)
# {
#     'A': 'Combien de fenêtres sur la façade?',
#     'B': 'Année de construction du bâtiment?',
#     'C': 'Numéro de la rue?'
# }
```

### Extraction depuis un objet Geocache

```python
from gc_backend.models.geocache import Geocache
from gc_backend.services.formula_questions_service import formula_questions_service

# Charger une géocache depuis la DB
geocache = Geocache.query.get(123)

# Extraire les questions depuis description + waypoints + hints
result = formula_questions_service.extract_questions_with_regex(
    geocache,
    ['A', 'B', 'C', 'D', 'E']
)

# Le service analyse automatiquement :
# - La description principale (HTML nettoyé)
# - Les notes des waypoints additionnels
# - Les hints (indices)
```

## Formats de questions supportés

### 1. Format standard avec séparateurs

```python
# Point
"A. Combien de fenêtres?"

# Double-points
"B: Année de construction?"

# Parenthèse fermante
"C) Numéro de la rue?"

# Tirets (-, –, —)
"D- Hauteur en mètres?"
"E– Code postal?"
"F— Distance en km?"

# Slash
"G/ Nombre d'étages?"
```

### 2. Format numéroté

```python
"""
Répondez aux questions suivantes:
1. (A) Combien de fenêtres sur la façade?
2. (B) Année de construction du bâtiment?
3. (C) Numéro de la rue?
"""
```

### 3. Format inverse (Question + Lettre)

```python
"""
Nombre de fenêtres A:
Année de construction B:
Numéro de la rue C:
"""
```

### 4. Questions multi-lignes

Le service gère les questions qui s'étendent sur plusieurs lignes :

```python
"""
A. Combien de fois le mot "geocaching"
   apparaît-il sur la plaque commémorative
   située à l'entrée du bâtiment?
"""
```

## API Complète

### `extract_questions_with_regex(content, letters)`

Extrait les questions pour les lettres spécifiées en utilisant des patterns regex.

**Paramètres** :
- `content` (str | Geocache) : Texte brut ou objet Geocache à analyser
- `letters` (List[str]) : Liste des lettres à rechercher (ex: `['A', 'B', 'C']`)

**Retour** :
- `Dict[str, str]` : Dictionnaire associant chaque lettre à sa question
  - Si une lettre n'est pas trouvée, la valeur est une chaîne vide `""`

**Exemple** :
```python
result = formula_questions_service.extract_questions_with_regex(
    "A. Question 1\nB. Question 2",
    ['A', 'B', 'C']
)
# {'A': 'Question 1', 'B': 'Question 2', 'C': ''}
```

### `extract_questions_with_ai(content, letters)`

**⚠️ NON IMPLÉMENTÉ** - Réservé pour une phase future.

Lève `NotImplementedError` si appelée.

## Comportements Spéciaux

### Gestion des doublons

Si plusieurs questions sont trouvées pour la même lettre, le service garde **la plus longue** (sauf pour le pattern "Question A:" qui a une priorité basse).

```python
text = """
A. Nombre
...
A. Nombre de fenêtres sur la façade principale du bâtiment
"""

result = formula_questions_service.extract_questions_with_regex(text, ['A'])
# {'A': 'Nombre de fenêtres sur la façade principale du bâtiment'}
```

### Nettoyage HTML

Le service nettoie automatiquement le HTML et préserve les sauts de ligne pour les éléments de bloc (`<li>`, `<p>`, `<div>`, `<br>`, `<h1-6>`, `<tr>`).

**Sans nettoyage :**
```html
<ul><li>A. Question 1</li><li>B. Question 2</li></ul>
→ "A. Question 1B. Question 2"  ❌ (pas de saut de ligne)
```

**Avec nettoyage :**
```html
<ul><li>A. Question 1</li><li>B. Question 2</li></ul>
→ "A. Question 1\nB. Question 2"  ✅
```

### Limitation de longueur

Pour éviter de surcharger le traitement, le contenu est limité à **10 000 caractères** maximum.

Si le contenu dépasse cette limite, il est tronqué avec un marqueur `[...TRONQUÉ...]`.

## Intégration avec Geocache

Le service extrait intelligemment le contenu des objets Geocache :

```python
# Structure du contenu extrait :
"""
=== DESCRIPTION PRINCIPALE ===
[Description HTML nettoyée]

=== WAYPOINTS ADDITIONNELS ===
WP1 - Nom du waypoint
Note: [Note du waypoint]

WP2 - Autre waypoint
Note: [Note du waypoint]

=== INDICE ===
[Hint nettoyé]
"""
```

## Tests

Le service est couvert par **23 tests unitaires** qui valident :

- ✅ Tous les formats de questions
- ✅ Nettoyage HTML (BeautifulSoup + fallback regex)
- ✅ Préparation de contenu (texte brut + Geocache)
- ✅ Gestion des cas limites (vide, partiel, doublons)

Exécuter les tests :
```bash
cd gc-backend
pytest gc_backend/services/tests/test_formula_questions_service.py -v
```

## Performances

- **Rapide** : Regex compilés à la volée
- **Léger** : Pas de dépendance IA (pour l'instant)
- **Robuste** : Fallback regex si BeautifulSoup n'est pas disponible

## Limitations

1. **Formats exotiques** : Certains formats très peu courants peuvent ne pas être détectés
2. **Contexte sémantique** : Pas de compréhension du sens des questions (résolu par IA dans une phase future)
3. **Questions imbriquées** : Les questions avec structure complexe peuvent être partiellement capturées

## Roadmap

- [ ] Support de l'extraction avec IA (GPT-4, Claude, etc.)
- [ ] Détection de variables dans les questions (ex: "A+B" dans une question)
- [ ] Suggestions de corrections pour questions mal formées
- [ ] Support multilingue (actuellement optimisé pour le français)

## Auteur

**MysterAI Team**  
Projet GeoApp - Formula Solver Module
