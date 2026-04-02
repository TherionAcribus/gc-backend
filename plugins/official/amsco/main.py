"""
Plugin Chiffre AMSCO pour MysterAI.

AMSCO (A.M.Scott, XIXe siècle) est un chiffrement par transposition colonnaire
à cellules mixtes : le texte est découpé alternativement en unigrammes (1 lettre)
et en bigrammes (2 lettres) lors du remplissage d'un tableau.

Principe :
  1. Remplir un tableau ligne par ligne avec des cellules alternant 1 et 2 caractères
     (diagonales identiques : cell(r,c) a la même taille si (r+c)%2 est identique)
  2. Lire les colonnes dans l'ordre alphabétique de la clé → texte chiffré

La séquence par défaut est 1,2 (unigramme d'abord) mais 2,1 est aussi possible.

Référence : https://www.dcode.fr/chiffre-amsco
"""

import string
import time
from typing import Any, Dict, List, Optional, Tuple


class AMSCOPlugin:
    """
    Plugin de chiffrement/déchiffrement AMSCO.

    Paramètres :
      - key          : clé de permutation colonnaire (mot, ex. « CLE »)
      - sequence     : « 1,2 » ou « 2,1 » (taille de la première cellule, défaut 1,2)
      - strip_spaces : supprimer les espaces du texte clair avant encodage (défaut true)
    """

    def __init__(self):
        self.name = "amsco"
        self.version = "1.0.0"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get('text', '').strip()
        mode = inputs.get('mode', 'decode').lower()
        key = inputs.get('key', '').strip().upper()
        sequence = inputs.get('sequence', '1,2').strip()
        strip_spaces = str(inputs.get('strip_spaces', 'true')).lower() != 'false'

        if not text:
            return self._error("Aucun texte fourni")
        if not key:
            return self._error("La clé de permutation (key) est obligatoire")

        start = self._parse_sequence(sequence)
        if start is None:
            return self._error("Séquence invalide. Utilisez '1,2' ou '2,1'.")

        try:
            if mode == 'encode':
                plain = text.upper()
                if strip_spaces:
                    plain = plain.replace(' ', '')
                result_text = self._encode(plain, key, start)
                summary = f"Texte encodé en AMSCO (clé: {key}, séq: {sequence})"
            elif mode == 'decode':
                clean = ''.join(c for c in text.upper() if c.isalpha())
                result_text = self._decode(clean, key, start)
                summary = f"Texte décodé depuis AMSCO (clé: {key}, séq: {sequence})"
            else:
                return self._error(f"Mode inconnu : {mode}. Utilisez 'encode' ou 'decode'.")
        except (ValueError, IndexError) as exc:
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
                    "key": key,
                    "sequence": sequence,
                },
                "metadata": {
                    "key_length": len(key),
                    "input_length": len(text),
                    "output_length": len(result_text),
                    "column_order": self._col_order_str(key),
                },
            }],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": round(execution_time, 2),
            },
        }

    def _parse_sequence(self, sequence: str) -> Optional[int]:
        """
        Parse la séquence de découpe.
        '1,2' ou '12' → start=1 (cellules: 1, 2, 1, 2, ...)
        '2,1' ou '21' → start=2 (cellules: 2, 1, 2, 1, ...)
        """
        s = sequence.replace(',', '').replace(' ', '')
        if s.startswith('1'):
            return 1
        if s.startswith('2'):
            return 2
        return None

    def _cell_size(self, row: int, col: int, start: int) -> int:
        """
        Taille de la cellule (row, col) selon le motif diagonal.
        start=1 → (r+c)%2==0: 1 lettre, (r+c)%2==1: 2 lettres
        start=2 → (r+c)%2==0: 2 lettres, (r+c)%2==1: 1 lettre
        """
        if (row + col) % 2 == 0:
            return start
        return 3 - start

    def _get_col_order(self, key: str) -> List[int]:
        """
        Retourne les indices de colonnes dans l'ordre alphabétique de la clé.
        Pour la clé 'CLE' : C < E < L → ordre [0, 2, 1] (indices de C, E, L)
        En cas de lettres identiques, l'ordre d'apparition prime.
        """
        return sorted(range(len(key)), key=lambda i: (key[i], i))

    def _col_order_str(self, key: str) -> str:
        """Représentation lisible de l'ordre des colonnes."""
        order = self._get_col_order(key)
        return ' → '.join(str(i + 1) for i in order)

    def _build_grid(self, total: int, n: int, start: int) -> Dict[Tuple[int, int], int]:
        """
        Calcule les tailles de chaque cellule de la grille.
        Retourne un dict {(row, col): size} pour toutes les cellules remplies.
        """
        grid_sizes: Dict[Tuple[int, int], int] = {}
        pos = 0
        row = 0
        while pos < total:
            for col in range(n):
                if pos >= total:
                    break
                size = self._cell_size(row, col, start)
                actual = min(size, total - pos)
                grid_sizes[(row, col)] = actual
                pos += actual
            row += 1
        return grid_sizes

    def _encode(self, text: str, key: str, start: int) -> str:
        """
        Chiffrement AMSCO.

        1. Remplit la grille ligne par ligne avec des cellules alternant 1/2 chars
        2. Lit les colonnes dans l'ordre alphabétique de la clé
        """
        n = len(key)
        total = len(text)
        if total == 0:
            raise ValueError("Texte vide après nettoyage.")

        col_order = self._get_col_order(key)
        grid_sizes = self._build_grid(total, n, start)

        grid: Dict[Tuple[int, int], str] = {}
        pos = 0
        for (row, col), size in sorted(grid_sizes.items()):
            grid[(row, col)] = text[pos:pos + size]
            pos += size

        result: List[str] = []
        for col_idx in col_order:
            row = 0
            while (row, col_idx) in grid:
                result.append(grid[(row, col_idx)])
                row += 1

        return ''.join(result)

    def _decode(self, ciphertext: str, key: str, start: int) -> str:
        """
        Déchiffrement AMSCO.

        1. Calcule la structure de la grille (tailles des cellules)
        2. Distribue le texte chiffré aux colonnes dans l'ordre alphabétique de la clé
        3. Lit la grille ligne par ligne pour retrouver le texte clair
        """
        n = len(key)
        total = len(ciphertext)
        if total == 0:
            raise ValueError("Texte vide.")

        col_order = self._get_col_order(key)
        grid_sizes = self._build_grid(total, n, start)

        col_char_count = [0] * n
        for (row, col), size in grid_sizes.items():
            col_char_count[col] += size

        col_data: Dict[int, str] = {}
        pos = 0
        for col_idx in col_order:
            size = col_char_count[col_idx]
            col_data[col_idx] = ciphertext[pos:pos + size]
            pos += size

        col_pos = [0] * n
        result: List[str] = []
        for (row, col), size in sorted(grid_sizes.items()):
            chunk = col_data[col][col_pos[col]:col_pos[col] + size]
            col_pos[col] += size
            result.append(chunk)

        return ''.join(result)

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
