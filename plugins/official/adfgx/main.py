"""
Plugin Chiffre ADFGX pour MysterAI.

Le chiffre ADFGX (1918) est un chiffrement militaire allemand de la Première Guerre
mondiale. Il combine :
  1. Une substitution par carré de Polybe 5×5 (alphabet A-Z, I=J)
  2. Une transposition colonnaire basée sur une clé mot

Les en-têtes de ligne et de colonne du carré sont les lettres A, D, F, G, X,
choisies pour leur faible risque de confusion en code Morse.

Le chiffre ADFGVX (variante à 6×6 incluant les chiffres) est disponible comme
plugin séparé.

Référence: https://fr.wikipedia.org/wiki/Chiffre_ADFGVX
"""

import string
import time
from typing import Any, Dict, List, Tuple


class ADFGXPlugin:
    """
    Plugin de chiffrement/déchiffrement ADFGX.

    Carré 5×5 (I=J), en-têtes : A D F G X
    Entrées nécessaires :
      - polybius_key : mot-clé pour mélanger l'alphabet du carré (optionnel)
      - transpo_key  : clé de transposition colonnaire (obligatoire)
    """

    HEADERS = ['A', 'D', 'F', 'G', 'X']
    GRID_SIZE = 5
    HEADER_SET = set(HEADERS)

    def __init__(self):
        self.name = "adfgx"
        self.version = "1.0.0"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get('text', '').strip()
        mode = inputs.get('mode', 'decode').lower()
        polybius_key = inputs.get('polybius_key', '').strip()
        transpo_key = inputs.get('transpo_key', '').strip().upper()

        if not text:
            return self._error("Aucun texte fourni")
        if not transpo_key:
            return self._error("La clé de transposition (transpo_key) est obligatoire")

        try:
            if mode == 'encode':
                result_text = self._encode(text, polybius_key, transpo_key)
                summary = "Texte encodé en ADFGX"
            elif mode == 'decode':
                result_text = self._decode(text, polybius_key, transpo_key)
                summary = "Texte décodé depuis ADFGX"
            else:
                return self._error(f"Mode inconnu : {mode}. Utilisez 'encode' ou 'decode'.")
        except ValueError as exc:
            return self._error(str(exc))

        execution_time = (time.time() - start_time) * 1000
        return {
            "status": "ok",
            "summary": summary,
            "results": [{
                "id": "result_1",
                "text_output": result_text,
                "confidence": 1.0,
                "parameters": {
                    "mode": mode,
                    "polybius_key": polybius_key or "(aucune)",
                    "transpo_key": transpo_key,
                },
                "metadata": {
                    "grid": "5x5 (I=J)",
                    "headers": "A D F G X",
                    "input_length": len(text),
                    "output_length": len(result_text),
                },
            }],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": round(execution_time, 2),
            },
        }

    def _build_square(self, key: str) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        Construit le carré de Polybe 5×5 à partir du mot-clé.

        L'alphabet (25 lettres, I=J) est réordonné : les lettres du mot-clé
        viennent en premier, puis les lettres restantes dans l'ordre naturel.

        Retourne :
            char_to_bigram : lettre → paire ADFGX (ex. 'A' → 'AD')
            bigram_to_char : paire ADFGX → lettre (ex. 'AD' → 'A')
        """
        alphabet = [c for c in string.ascii_uppercase if c != 'J']  # 25 lettres
        key = key.upper().replace('J', 'I')

        seen: set = set()
        ordered: List[str] = []
        for c in key:
            if c in alphabet and c not in seen:
                ordered.append(c)
                seen.add(c)
        ordered += [c for c in alphabet if c not in seen]

        n = self.GRID_SIZE
        char_to_bigram: Dict[str, str] = {}
        bigram_to_char: Dict[str, str] = {}

        for idx, c in enumerate(ordered[:n * n]):
            row = idx // n
            col = idx % n
            bigram = self.HEADERS[row] + self.HEADERS[col]
            char_to_bigram[c] = bigram
            bigram_to_char[bigram] = c

        char_to_bigram['J'] = char_to_bigram.get('I', '??')
        return char_to_bigram, bigram_to_char

    def _substitute(self, text: str, char_to_bigram: Dict[str, str]) -> str:
        """Étape 1 d'encodage : chaque lettre → bigrame ADFGX."""
        result: List[str] = []
        for c in text.upper():
            if c == 'J':
                c = 'I'
            if c in char_to_bigram:
                result.append(char_to_bigram[c])
        return ''.join(result)

    def _reverse_substitute(self, text: str, bigram_to_char: Dict[str, str]) -> str:
        """Reverse substitution : bigrammes ADFGX → lettres."""
        result: List[str] = []
        for i in range(0, len(text) - 1, 2):
            bigram = text[i] + text[i + 1]
            result.append(bigram_to_char.get(bigram, '?'))
        return ''.join(result)

    def _columnar_transpose(self, text: str, key: str) -> str:
        """
        Transposition colonnaire.

        Le texte est écrit en lignes sous la clé.
        Les colonnes sont lues dans l'ordre alphabétique des lettres de la clé.
        En cas de lettres identiques, l'ordre d'apparition (index) est utilisé.
        """
        n = len(key)
        col_order = sorted(range(n), key=lambda i: (key[i], i))

        rows = [text[i:i + n] for i in range(0, len(text), n)]

        result: List[str] = []
        for col_idx in col_order:
            for row in rows:
                if col_idx < len(row):
                    result.append(row[col_idx])
        return ''.join(result)

    def _reverse_columnar_transpose(self, text: str, key: str) -> str:
        """
        Transposition colonnaire inverse.

        Reconstruit l'ordre original des colonnes à partir de la clé.
        """
        n = len(key)
        total = len(text)
        if total == 0:
            return ''

        num_rows = (total + n - 1) // n
        extra = total % n  # nombre de colonnes "hautes" (num_rows lignes)

        col_order = sorted(range(n), key=lambda i: (key[i], i))

        col_data: Dict[int, List[str]] = {}
        pos = 0
        for rank, orig_col in enumerate(col_order):
            if extra == 0 or orig_col < extra:
                length = num_rows
            else:
                length = num_rows - 1
            col_data[orig_col] = list(text[pos:pos + length])
            pos += length

        result: List[str] = []
        for row in range(num_rows):
            for col in range(n):
                if row < len(col_data[col]):
                    result.append(col_data[col][row])
        return ''.join(result)

    def _encode(self, text: str, polybius_key: str, transpo_key: str) -> str:
        char_to_bigram, _ = self._build_square(polybius_key)
        substituted = self._substitute(text, char_to_bigram)
        if not substituted:
            raise ValueError("Aucun caractère encodable trouvé dans le texte.")
        transposed = self._columnar_transpose(substituted, transpo_key)
        return ' '.join(transposed[i:i + 2] for i in range(0, len(transposed), 2))

    def _decode(self, text: str, polybius_key: str, transpo_key: str) -> str:
        clean = ''.join(c for c in text.upper() if c in self.HEADER_SET)
        if not clean:
            raise ValueError("Aucune lettre ADFGX valide trouvée dans le texte.")
        if len(clean) % 2 != 0:
            raise ValueError(
                f"Longueur impaire ({len(clean)} caractères ADFGX). "
                "Le texte ADFGX doit avoir un nombre pair de caractères."
            )
        _, bigram_to_char = self._build_square(polybius_key)
        detransposed = self._reverse_columnar_transpose(clean, transpo_key)
        return self._reverse_substitute(detransposed, bigram_to_char)

    def _error(self, message: str) -> Dict[str, Any]:
        return {
            "status": "error",
            "summary": message,
            "results": [],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": 0,
            },
        }
