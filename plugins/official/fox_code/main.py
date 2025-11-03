"""
Plugin Fox Code pour MysterAI

Encodage et décodage du Fox Code utilisant une grille 3×9.
Deux variantes:
  - Courte: encode uniquement le numéro de colonne (1-9)
  - Longue: encode ligne+colonne (ex: 71 → colonne 7, ligne 1)

Référence: https://www.dcode.fr/code-fox
"""

import re
import time
from itertools import product
from typing import Dict, List, Tuple


class FoxCodePlugin:
    """Plugin de codage/décodage pour le Fox Code."""

    def __init__(self):
        self.name = "fox_code"
        self.description = "Plugin d'encodage/décodage du Fox Code"

        # Construction de la grille 3×9
        self._GRID: Dict[Tuple[int, int], str] = {}
        self._LETTER_TO_POS: Dict[str, Tuple[int, int]] = {}
        self._ROWS, self._COLS = 3, 9
        self._ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        for idx, letter in enumerate(self._ALPHABET):
            row = idx // self._COLS + 1  # 1-indexé
            col = idx % self._COLS + 1
            # La 3ᵉ ligne n'a que 8 colonnes (pas de lettre pour 3,9)
            if row == 3 and col == 9:
                break
            self._GRID[(row, col)] = letter
            self._LETTER_TO_POS[letter] = (row, col)

    # -------------------------------------------------------------------------
    # Encodage
    # -------------------------------------------------------------------------
    def _encode_char(self, char: str, variant: str) -> str:
        """Encode un caractère selon la variante."""
        if char.upper() not in self._LETTER_TO_POS:
            return char  # Caractère non pris en charge - on le conserve

        row, col = self._LETTER_TO_POS[char.upper()]

        if variant == "short":
            return str(col)

        # Variante longue: ligne puis colonne
        return f"{row}{col}"

    def encode(self, text: str, variant: str = "long") -> str:
        """Encode le texte en Fox Code."""
        variant = variant.lower()
        if variant not in ("short", "long"):
            variant = "long"

        encoded_tokens = [self._encode_char(ch, variant) for ch in text]
        # Séparer les jetons par des espaces pour plus de lisibilité
        return " ".join(encoded_tokens)

    # -------------------------------------------------------------------------
    # Décodage
    # -------------------------------------------------------------------------
    def _decode_long_tokens(self, tokens: List[str]) -> str:
        """Décode les tokens de la variante longue."""
        result = []
        for tok in tokens:
            if not re.fullmatch(r"[1-3][1-9]", tok):
                # Jeton invalide – on le renvoie tel quel
                result.append(tok)
                continue

            row = int(tok[0])
            col = int(tok[1])
            letter = self._GRID.get((row, col), "?")
            result.append(letter)

        return "".join(result)

    def _generate_short_decodings(self, digits: List[str], limit: int = 20) -> List[str]:
        """Génère toutes les décodages possibles de la variante courte (limité)."""
        # Pour chaque chiffre → liste des lettres possibles (1-3 lignes)
        letter_options = []
        for d in digits:
            if d not in "123456789":
                letter_options.append([d])
                continue

            col = int(d)
            opts = [self._GRID.get((row, col)) for row in range(1, 4)]
            # Filtrer None (ex: colonne 9 ligne 3 inexistante)
            opts = [o for o in opts if o]
            letter_options.append(opts)

        # Produit cartésien – peut exploser, on limite
        possibilities = []
        for combination in product(*letter_options):
            possibilities.append("".join(combination))
            if len(possibilities) >= limit:
                break

        return possibilities

    def decode(self, text: str, variant: str = "auto") -> List[str]:
        """Décode le texte. Renvoie une liste de décodages possibles."""
        variant = variant.lower()

        # Nettoyage: on conserve chiffres et séparateurs espace
        cleaned = re.sub(r"[^0-9\s]", " ", text)
        cleaned = re.sub(r"\s+", " ", cleaned.strip())

        # Détection automatique ou variante longue
        if variant == "long" or (variant == "auto" and re.search(r"[1-3][1-9](\s|$)", cleaned)):
            # Tokens séparés par espace OU concaténés 2 par 2
            if " " in cleaned:
                tokens = cleaned.split()
            else:
                tokens = [cleaned[i:i+2] for i in range(0, len(cleaned), 2)]
            return [self._decode_long_tokens(tokens)]

        # Variante courte
        digits = cleaned.split() if " " in cleaned else list(cleaned)
        return self._generate_short_decodings(digits)

    # -------------------------------------------------------------------------
    # Méthode execute (interface standardisée)
    # -------------------------------------------------------------------------
    def execute(self, inputs: Dict) -> Dict:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        mode = inputs.get("mode", "decode").lower()
        text = inputs.get("text", "")
        variant = inputs.get("variant", "auto").lower()

        response = {
            "status": "ok",
            "plugin_info": {
                "name": self.name,
                "version": "1.1.0",
                "execution_time_ms": 0
            },
            "results": [],
            "summary": ""
        }

        if mode == "encode":
            # Pour l'encodage, si auto on utilise la variante longue
            encode_variant = "long" if variant == "auto" else variant
            encoded = self.encode(text, variant=encode_variant)

            result = {
                "id": "result_1",
                "text_output": encoded,
                "confidence": 1.0,
                "parameters": {
                    "mode": "encode",
                    "variant": encode_variant
                },
                "metadata": {}
            }
            response["results"].append(result)
            response["summary"] = "Encodage réussi"

        elif mode == "decode":
            decodings = self.decode(text, variant=variant)

            for idx, dec in enumerate(decodings[:10]):  # Limite de sécurité
                conf = 0.8 if idx == 0 else 0.5 - (idx * 0.05)
                if conf < 0.1:
                    conf = 0.1

                result = {
                    "id": f"result_{idx+1}",
                    "text_output": dec,
                    "confidence": conf,
                    "parameters": {
                        "mode": "decode",
                        "variant": variant
                    },
                    "metadata": {
                        "candidate_rank": idx + 1
                    }
                }
                response["results"].append(result)

            response["summary"] = f"{len(response['results'])} décodage(s) possible(s)"

        else:
            response["status"] = "error"
            response["summary"] = f"Mode '{mode}' non pris en charge."

        execution_time = int((time.time() - start_time) * 1000)
        response["plugin_info"]["execution_time_ms"] = execution_time

        return response


def execute(inputs: dict) -> dict:
    """Point d'entrée pour le système de plugins."""
    plugin = FoxCodePlugin()
    return plugin.execute(inputs)
