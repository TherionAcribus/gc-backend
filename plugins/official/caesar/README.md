# Plugin Caesar

Plugin de chiffrement et déchiffrement Caesar (ROT-N) pour MysterAI.

## Description

Le chiffrement Caesar est une technique de chiffrement par substitution dans laquelle chaque lettre du texte est décalée d'un nombre fixe de positions dans l'alphabet.

**Exemple avec ROT-13** :
- `A` → `N`
- `B` → `O`
- `HELLO` → `URYYB`

## Fonctionnalités

- ✅ **Encodage** : Chiffrer du texte avec un décalage personnalisé
- ✅ **Décodage** : Déchiffrer du texte en connaissant le décalage
- ✅ **Bruteforce** : Tester tous les décalages possibles (1-25)
- ✅ **Détection** : Déterminer si un texte pourrait être du code Caesar

## Paramètres

### Entrées

| Paramètre | Type | Description | Défaut |
|-----------|------|-------------|--------|
| `text` | string | Texte à traiter | - |
| `mode` | select | Mode d'opération (encode, decode, detect) | decode |
| `shift` | number | Décalage dans l'alphabet (1-25) | 13 |
| `brute_force` | boolean | Activer le mode bruteforce | false |

### Sorties

Le plugin retourne un résultat au format standardisé :

```json
{
  "status": "ok",
  "summary": "Message résumé",
  "results": [
    {
      "id": "result_1",
      "text_output": "TEXTE DÉCODÉ",
      "confidence": 0.85,
      "parameters": {
        "mode": "decode",
        "shift": 13
      },
      "metadata": {
        "processed_chars": 12
      }
    }
  ],
  "plugin_info": {
    "name": "caesar",
    "version": "1.0.0",
    "execution_time_ms": 5.23
  }
}
```

## Exemples d'utilisation

### Encodage simple (ROT-13)

**Entrée** :
```json
{
  "text": "HELLO WORLD",
  "mode": "encode",
  "shift": 13
}
```

**Sortie** :
```
URYYB JBEYQ
```

### Décodage simple

**Entrée** :
```json
{
  "text": "URYYB JBEYQ",
  "mode": "decode",
  "shift": 13
}
```

**Sortie** :
```
HELLO WORLD
```

### Mode Bruteforce

Teste automatiquement tous les décalages de 1 à 25 et retourne 25 résultats.

**Entrée** :
```json
{
  "text": "URYYB",
  "mode": "decode",
  "brute_force": true
}
```

**Sortie** :
25 résultats avec différents décalages. Le scoring service les classera par pertinence.

## Algorithme

Le plugin utilise une simple arithmétique modulaire :

```
lettre_chiffrée = (lettre_originale + décalage) % 26
lettre_déchiffrée = (lettre_chiffrée - décalage) % 26
```

Les caractères non alphabétiques (chiffres, ponctuation, espaces) sont conservés tels quels.

## Catégories

- **Substitution** : Chiffrement par substitution
- **Caesar** : Spécifique au chiffre Caesar

## Notes

- Le plugin ne gère pas les caractères accentués (`accept_accents: false`)
- La casse est préservée (majuscules/minuscules)
- Le scoring automatique est activé pour évaluer la pertinence des résultats bruteforce
- Non CPU intensif (`heavy_cpu: false`)

## Historique des versions

### 1.0.0 (2025-11-02)
- Implémentation initiale
- Support encode, decode, detect
- Mode bruteforce
- Conformité plugin_api_version 2.0
