"""
Plugin Chiffre de Bazeries pour MysterAI.

Étienne Bazeries (~1890). Chiffrement combinant :
  1. Substitution par double grille de Polybe 5×5 (I=J)
     - Grille 1 : alphabet standard rempli PAR COLONNES (A en (0,0), B en (1,0), ...)
     - Grille 2 : alphabet dérivé de la clé, rempli PAR LIGNES
  2. Transposition : le texte est découpé en groupes dont la taille correspond aux
     chiffres successifs (cycliques) de la clé numérique, puis chaque groupe est inversé.

Ordre d'encodage : groupe+inversion → substitution Grid1→Grid2
Ordre de décodage : groupe+inversion → substitution Grid2→Grid1

Exemple dcode.fr : DCODE + N=23 (VINGTTROIS) → CKUKM

Référence : https://www.dcode.fr/chiffre-bazeries
"""

import time
from typing import Any, Dict, List, Optional, Tuple


class BazeriesPlugin:
    """
    Plugin de chiffrement/déchiffrement par le chiffre de Bazeries.

    Paramètres :
      - key         : clé numérique OBLIGATOIRE (nombre entier 1–9999, ex: « 23 »)
                      Les chiffres du nombre définissent les groupes de transposition.
                      L'écriture en français génère automatiquement la Grille 2.
      - polybius_key: mot-clé optionnel pour la Grille 2 (surcharge la dérivation française)
    """

    ALPHABET = "ABCDEFGHIKLMNOPQRSTUVWXYZ"

    def __init__(self):
        self.name = "bazeries"
        self.version = "1.0.0"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get('text', '').strip()
        mode = inputs.get('mode', 'decode').lower()
        key = inputs.get('key', '').strip()
        polybius_key_override = inputs.get('polybius_key', '').strip()

        if not text:
            return self._error("Aucun texte fourni")
        if not key:
            return self._error("Clé numérique manquante. Fournissez 'key' (ex: 23, 314...).")
        if not key.isdigit():
            return self._error(
                f"La clé '{key}' n'est pas un nombre. "
                "Le chiffre de Bazeries requiert un nombre entier (ex: 23, 314, 1914...). "
                "Les chiffres définissent les groupes et leur écriture en français génère la grille."
            )

        polybius_word, transpo_digits, display_key = self._resolve_keys(
            key, polybius_key_override
        )

        if polybius_word is None or transpo_digits is None:
            return self._error(f"Clé invalide : {key}. Utilisez un nombre entre 1 et 9999.")

        try:
            clean = ''.join(c for c in text.upper().replace('J', 'I') if c.isalpha())
            if not clean:
                return self._error("Aucun caractère alphabétique trouvé dans le texte.")

            if mode == 'encode':
                result_text = self._encode(clean, polybius_word, transpo_digits)
                summary = f"Texte encodé par Bazeries (clé: {display_key})"
            elif mode == 'decode':
                result_text = self._decode(clean, polybius_word, transpo_digits)
                summary = f"Texte décodé depuis Bazeries (clé: {display_key})"
            else:
                return self._error(f"Mode inconnu : {mode}. Utilisez 'encode' ou 'decode'.")
        except (ValueError, KeyError) as exc:
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
                    "polybius_key": polybius_word,
                    "transpo_digits": ''.join(str(d) for d in transpo_digits),
                },
                "metadata": {
                    "grid": "5x5 (I=J)",
                    "grid1_fill": "par colonnes (standard)",
                    "grid2_fill": "par lignes (clé)",
                    "input_length": len(clean),
                    "output_length": len(result_text),
                },
            }],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": round(execution_time, 2),
            },
        }

    def _resolve_keys(
        self,
        key: str,
        polybius_override: str,
    ) -> Tuple[Optional[str], Optional[List[int]], str]:
        """
        Résout la clé Polybe et les chiffres de transposition.

        key doit être un nombre entier (1–9999).
        polybius_override, si fourni, remplace la dérivation française pour la Grille 2.

        Retourne : (polybius_word, transpo_digits, display_key)
        """
        n = int(key)
        try:
            french_word = self._number_to_french(n)
        except ValueError:
            return None, None, key

        polybius_word = ''.join(c for c in polybius_override.upper() if c.isalpha()) \
            if polybius_override else french_word

        transpo_digits = [int(d) for d in key if d != '0']
        if not transpo_digits:
            transpo_digits = [int(key[-1])]

        display_key = f"{key} ({french_word})"
        if polybius_override:
            display_key += f" [Polybe: {polybius_word}]"

        return polybius_word, transpo_digits, display_key

    def _number_to_french(self, n: int) -> str:
        """
        Convertit un nombre (1–9999) en sa représentation française majuscule sans espaces.

        Exemples :
          23   → VINGTTROIS
          123  → CENTVINGTTROIS
          1914 → MILLENEUTCENTQUATORZE
        """
        if n < 1 or n > 9999:
            raise ValueError(f"Nombre {n} hors de la plage supportée (1–9999).")

        units = ["", "UN", "DEUX", "TROIS", "QUATRE", "CINQ", "SIX", "SEPT", "HUIT", "NEUF"]
        teens = ["DIX", "ONZE", "DOUZE", "TREIZE", "QUATORZE", "QUINZE", "SEIZE",
                 "DIXSEPT", "DIXHUIT", "DIXNEUF"]
        tens_w = ["", "DIX", "VINGT", "TRENTE", "QUARANTE", "CINQUANTE", "SOIXANTE"]

        def below100(x: int) -> str:
            if x == 0:
                return ""
            if x < 10:
                return units[x]
            if x < 20:
                return teens[x - 10]
            if x < 60:
                t, u = divmod(x, 10)
                base = tens_w[t]
                if u == 0:
                    return base
                if u == 1:
                    return base + "ETUN"
                return base + units[u]
            if x < 70:
                u = x - 60
                if u == 0:
                    return "SOIXANTE"
                if u == 1:
                    return "SOIXANTEETONZE"
                if u < 10:
                    return "SOIXANTE" + units[u]
                return "SOIXANTE" + teens[u - 10]
            if x < 80:
                return "SOIXANTE" + teens[x - 70]
            if x < 90:
                u = x - 80
                return "QUATREVINGTS" if u == 0 else "QUATREVINGT" + units[u]
            return "QUATREVINGT" + teens[x - 90]

        def below1000(x: int) -> str:
            if x == 0:
                return ""
            h, r = divmod(x, 100)
            result = ""
            if h == 1:
                result = "CENT"
            elif h > 1:
                result = units[h] + "CENT"
                if r == 0:
                    result += "S"
            result += below100(r)
            return result

        if n >= 1000:
            th, r = divmod(n, 1000)
            prefix = "MILLE" if th == 1 else below100(th) + "MILLE"
            return prefix + below1000(r)
        return below1000(n)

    def _build_grid1(self) -> Tuple[Dict[str, Tuple[int, int]], Dict[Tuple[int, int], str]]:
        """
        Grille 1 : alphabet standard (A–Z, sans J) rempli PAR COLONNES.

        Lettre à l'index i : row = i % 5, col = i // 5
        Grille résultante :
          A F L Q V
          B G M R W
          C H N S X
          D I O T Y
          E K P U Z
        """
        char_to_pos: Dict[str, Tuple[int, int]] = {}
        pos_to_char: Dict[Tuple[int, int], str] = {}
        for idx, c in enumerate(self.ALPHABET):
            row, col = idx % 5, idx // 5
            char_to_pos[c] = (row, col)
            pos_to_char[(row, col)] = c
        char_to_pos['J'] = char_to_pos['I']
        return char_to_pos, pos_to_char

    def _build_grid2(self, key: str) -> Tuple[Dict[str, Tuple[int, int]], Dict[Tuple[int, int], str]]:
        """
        Grille 2 : alphabet dérivé de la clé, rempli PAR LIGNES.

        Les lettres de la clé (sans doublons, I=J) viennent en premier,
        suivies des lettres restantes de l'alphabet dans l'ordre naturel.
        Lettre à l'index i : row = i // 5, col = i % 5
        """
        key_clean = key.upper().replace('J', 'I')
        seen: set = set()
        ordered: List[str] = []
        for c in key_clean:
            if c in self.ALPHABET and c not in seen:
                ordered.append(c)
                seen.add(c)
        for c in self.ALPHABET:
            if c not in seen:
                ordered.append(c)

        char_to_pos: Dict[str, Tuple[int, int]] = {}
        pos_to_char: Dict[Tuple[int, int], str] = {}
        for idx, c in enumerate(ordered[:25]):
            row, col = idx // 5, idx % 5
            char_to_pos[c] = (row, col)
            pos_to_char[(row, col)] = c
        char_to_pos['J'] = char_to_pos.get('I', (0, 0))
        return char_to_pos, pos_to_char

    def _group_reverse(self, text: str, digits: List[int]) -> str:
        """
        Découpe le texte en groupes selon les chiffres (cycliques) et inverse chaque groupe.
        Opération involutive : appliquer deux fois redonne le texte original.
        """
        result: List[str] = []
        pos = 0
        n = len(digits)
        di = 0
        while pos < len(text):
            size = max(1, digits[di % n])
            group = text[pos:pos + size]
            result.append(group[::-1])
            pos += size
            di += 1
        return ''.join(result)

    def _encode(self, text: str, polybius_key: str, transpo_digits: List[int]) -> str:
        """
        Encodage Bazeries :
          1. Découpage par chiffres de la clé + inversion de chaque groupe
          2. Substitution lettre par lettre : position Grille1 → lettre Grille2
        """
        g1_char_to_pos, _ = self._build_grid1()
        _, g2_pos_to_char = self._build_grid2(polybius_key)

        transposed = self._group_reverse(text, transpo_digits)

        result: List[str] = []
        for c in transposed:
            c = c.replace('J', 'I')
            if c in g1_char_to_pos:
                pos = g1_char_to_pos[c]
                result.append(g2_pos_to_char[pos])
        return ''.join(result)

    def _decode(self, text: str, polybius_key: str, transpo_digits: List[int]) -> str:
        """
        Décodage Bazeries :
          1. Découpage par chiffres de la clé + inversion de chaque groupe
          2. Substitution lettre par lettre : position Grille2 → lettre Grille1
        """
        g2_char_to_pos, _ = self._build_grid2(polybius_key)
        _, g1_pos_to_char = self._build_grid1()

        transposed = self._group_reverse(text, transpo_digits)

        result: List[str] = []
        for c in transposed:
            c = c.replace('J', 'I')
            if c in g2_char_to_pos:
                pos = g2_char_to_pos[c]
                result.append(g1_pos_to_char[pos])
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
