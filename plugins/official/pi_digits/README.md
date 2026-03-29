# Plugin Pi Digits

## Description

Ce plugin permet de trouver les chiffres de Pi en fonction de leur position dans les décimales.

Par exemple :
- Position 1 → 1 (première décimale de π = 3.1415...)
- Position 49 → 2 (49ème décimale)
- Position 100 → 9 (100ème décimale)

## Utilisation

### Entrées

- **text** : Positions à rechercher (séparées par des espaces, virgules ou retours à la ligne)
  - Exemple : `49 100 1000`
  - Exemple : `1, 2, 3, 4, 5`
  - Exemple avec coordonnées : `N 48° 51.400` → extrait `48 51 400`

- **format** : Format de sortie
  - `digits_only` (défaut) : Uniquement les chiffres concaténés
  - `positions_and_digits` : Format `position=chiffre`
  - `detailed` : Tableau détaillé avec une ligne par position

- **allowed_chars** : Caractères à ignorer lors du parsing (défaut : ` \t\r\n.°NSEW`)
  - Permet de traiter des textes contenant des coordonnées GPS
  - Les caractères cardinaux (N, S, E, W), les points, les degrés (°) et les espaces sont ignorés par défaut
  - Exemple : `N 48° 5A.BCD` avec A=49, B=100, C=1000 → `49 100 1000`

### Exemples

#### Exemple 1 : Format digits_only
**Entrée :** `49 100 1000`  
**Sortie :** `294`

#### Exemple 2 : Format positions_and_digits
**Entrée :** `1 2 3 4 5`  
**Sortie :** `1=1 2=4 3=1 4=5 5=9`

#### Exemple 3 : Format detailed
**Entrée :** `49 100`  
**Sortie :**
```
Position 49: 2
Position 100: 9
```

#### Exemple 4 : Avec coordonnées GPS
**Entrée :** `N 48° 51.400`  
**Sortie (digits_only) :** `1815400`  
**Explication :** Les caractères N, °, et . sont ignorés, on extrait les positions 48, 51, 400

#### Exemple 5 : Formule de géocache
**Entrée :** `N 48° 5A.BCD E 002° 21.EFG` avec A=49, B=100, C=1000, etc.  
**Avec positions :** `49 100 1000`  
**Sortie :** `294` (49ème=2, 100ème=9, 1000ème=4)

## Limites

Le plugin contient les **1000 premières décimales** de Pi. Les positions au-delà de 1000 seront ignorées avec un avertissement dans les métadonnées.

## Cas d'usage en géocaching

Ce plugin est utile pour résoudre des énigmes où les coordonnées sont encodées avec des positions dans Pi :

- `N 48° 5A.BCD` où A=49ème décimale, B=100ème, etc.
- Codes utilisant Pi comme table de substitution

## Métadonnées retournées

Le plugin retourne des métadonnées détaillées :
- `total_positions` : Nombre total de positions demandées
- `valid_positions` : Nombre de positions valides trouvées
- `invalid_positions` : Liste des positions invalides (hors limites)
- `max_available_position` : Position maximale disponible (1000)
- `positions_data` : Tableau détaillé avec position et chiffre pour chaque résultat

## Metasolver

Ce plugin est éligible au metasolver avec :
- **input_charset** : `digits`
- **tags** : `numeral`, `no_key`
- **priority** : 60
