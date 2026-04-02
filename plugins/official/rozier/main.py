"""
Plugin Chiffre de Rozier pour MysterAI.

Le chiffre de Rozier est une variante du chiffre de Vigenère.
Pour chaque lettre du message à la position i, on utilise les lettres
consécutives key[i] et key[i+1] de la clé (cyclique).

Formules :
  Encodage : C[i] = (P[i] + key[(i+1)%n] - key[i%n]) % 26
  Décodage : P[i] = (C[i] - key[(i+1)%n] + key[i%n]) % 26

Équivalence : Rozier(M, K) ≡ Vigenère(M, K') où
  K'[i] = (key[(i+1)%n] - key[i%n]) % 26

Exemple dcode.fr : ROZIER + clé DCODE → QAOJDQ

Référence : https://www.dcode.fr/chiffre-rozier
"""

import time
from typing import Any, Dict, List, Tuple


class RozierPlugin:
    """
    Plugin de chiffrement/déchiffrement par le chiffre de Rozier.

    Paramètres :
      - key           : clé alphabétique (≥ 2 lettres, ex: DCODE)
      - preserve_case : conserver la casse du texte en sortie (défaut true)
      - keep_spaces   : conserver espaces et ponctuation (défaut true)
    """

    def __init__(self) -> None:
        self.name = "rozier"
        self.version = "1.0.0"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get('text', '')
        mode = inputs.get('mode', 'decode').lower()
        key = inputs.get('key', '').strip()
        preserve_case = str(inputs.get('preserve_case', 'true')).lower() != 'false'
        keep_spaces = str(inputs.get('keep_spaces', 'true')).lower() != 'false'

        if not text:
            return self._error("Aucun texte fourni")
        if not key:
            return self._error("Clé manquante. Fournissez 'key' (ex: DCODE).")

        key_clean = ''.join(c for c in key.upper() if c.isalpha())
        if not key_clean:
            return self._error("La clé ne contient aucune lettre.")
        if len(key_clean) < 2:
            return self._error(
                "La clé doit contenir au moins 2 lettres. "
                "Avec une seule lettre, key[i] = key[i+1] et le décalage est nul (texte inchangé)."
            )

        try:
            if mode == 'encode':
                result_text = self._process(text, key_clean, encode=True,
                                            preserve_case=preserve_case, keep_spaces=keep_spaces)
                summary = f"Texte encodé par Rozier (clé: {key_clean})"
            elif mode == 'decode':
                result_text = self._process(text, key_clean, encode=False,
                                            preserve_case=preserve_case, keep_spaces=keep_spaces)
                summary = f"Texte décodé depuis Rozier (clé: {key_clean})"
            else:
                return self._error(f"Mode inconnu : {mode}. Utilisez 'encode' ou 'decode'.")
        except Exception as exc:
            return self._error(str(exc))

        vigenere_key = self._to_vigenere_key(key_clean)
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
                    "key": key_clean,
                    "vigenere_equivalent_key": vigenere_key,
                },
                "metadata": {
                    "key_length": len(key_clean),
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

    def _process(self, text: str, key: str, encode: bool,
                 preserve_case: bool, keep_spaces: bool) -> str:
        """
        Applique le chiffrement ou déchiffrement de Rozier lettre par lettre.

        Seules les lettres sont transformées. Les autres caractères sont
        soit conservés (keep_spaces=True) soit supprimés.
        """
        n = len(key)
        key_vals = [ord(c) - ord('A') for c in key]

        result: List[str] = []
        alpha_idx = 0

        for ch in text:
            if ch.isalpha():
                is_lower = ch.islower()
                val = ord(ch.upper()) - ord('A')
                i = alpha_idx % n
                k0 = key_vals[i]
                k1 = key_vals[(i + 1) % n]

                if encode:
                    new_val = (val + k1 - k0) % 26
                else:
                    new_val = (val - k1 + k0) % 26

                new_ch = chr(new_val + ord('A'))
                if preserve_case and is_lower:
                    new_ch = new_ch.lower()
                result.append(new_ch)
                alpha_idx += 1
            else:
                if keep_spaces:
                    result.append(ch)

        return ''.join(result)

    def _to_vigenere_key(self, key: str) -> str:
        """
        Convertit la clé Rozier en clé Vigenère équivalente.

        vigenere_key[i] = (key[(i+1)%n] - key[i%n]) % 26

        Exemple : KEY → UUM
          K(10), E(4), Y(24)
          (4-10)%26=20=U, (24-4)%26=20=U, (10-24)%26=12=M
        """
        n = len(key)
        vals = [ord(c) - ord('A') for c in key]
        return ''.join(chr(((vals[(i + 1) % n] - vals[i]) % 26) + ord('A')) for i in range(n))

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
