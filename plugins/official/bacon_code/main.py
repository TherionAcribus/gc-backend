"""
Plugin Bacon Code pour MysterAI

Chiffrement et déchiffrement utilisant le chiffre Bacon (bilitère).
Chaque lettre est remplacée par une séquence de 5 symboles A/B.

Référence: https://www.dcode.fr/chiffre-bacon-bilitere
"""

import time
from typing import Dict, List, Tuple


class BaconCodePlugin:
    """Plugin pour encoder/décoder le chiffre de Bacon."""

    def __init__(self):
        self.name = "bacon_code"
        self.description = "Plugin de chiffrement/déchiffrement utilisant le chiffre Bacon (bilitère)"

        # Tables de substitution - Variante 26 lettres (I≠J, U≠V)
        self.encode_table_26 = {
            'A': 'AAAAA', 'B': 'AAAAB', 'C': 'AAABA', 'D': 'AAABB', 'E': 'AABAA',
            'F': 'AABAB', 'G': 'AABBA', 'H': 'AABBB', 'I': 'ABAAA', 'J': 'ABAAB',
            'K': 'ABABA', 'L': 'ABABB', 'M': 'ABBAA', 'N': 'ABBAB', 'O': 'ABBBA',
            'P': 'ABBBB', 'Q': 'BAAAA', 'R': 'BAAAB', 'S': 'BAABA', 'T': 'BAABB',
            'U': 'BABAA', 'V': 'BABAB', 'W': 'BABBA', 'X': 'BABBB', 'Y': 'BBAAA',
            'Z': 'BBAAB'
        }

        # Variante 24 lettres (I=J, U=V)
        self.encode_table_24 = {
            'A': 'AAAAA', 'B': 'AAAAB', 'C': 'AAABA', 'D': 'AAABB', 'E': 'AABAA',
            'F': 'AABAB', 'G': 'AABBA', 'H': 'AABBB', 'I': 'ABAAA', 'J': 'ABAAA',
            'K': 'ABAAB', 'L': 'ABABA', 'M': 'ABABB', 'N': 'ABBAA', 'O': 'ABBAB',
            'P': 'ABBBA', 'Q': 'ABBBB', 'R': 'BAAAA', 'S': 'BAAAB', 'T': 'BAABA',
            'U': 'BAABB', 'V': 'BAABB', 'W': 'BABAA', 'X': 'BABAB', 'Y': 'BABBA',
            'Z': 'BABBB'
        }

        # Paramètres par défaut
        self.variant = "26"
        self.symbol_a = "A"
        self.symbol_b = "B"
        self.default_separators = " \t\r\n.:;,_-'\"!?"

    def _build_decode_table(self, encode_table: Dict[str, str]) -> Dict[str, str]:
        """Construit la table de décodage à partir de la table d'encodage."""
        return {v: k for k, v in encode_table.items()}

    def _to_canonical_ab(self, text: str) -> str:
        """Convertit le texte en alphabet canonique A/B."""
        if self.symbol_a == "A" and self.symbol_b == "B":
            return text

        # Utiliser un caractère temporaire pour éviter les collisions
        tmp = "\uFFF0"
        converted = text.replace(self.symbol_a, tmp)
        converted = converted.replace(self.symbol_b, "B")
        converted = converted.replace(tmp, "A")
        return converted

    def _from_canonical_ab(self, text: str) -> str:
        """Convertit un texte A/B vers les symboles personnalisés."""
        if self.symbol_a == "A" and self.symbol_b == "B":
            return text

        tmp = "\uFFF0"
        converted = text.replace("A", tmp)
        converted = converted.replace("B", self.symbol_b)
        converted = converted.replace(tmp, self.symbol_a)
        return converted

    def encode(self, text: str) -> str:
        """Encode le texte en chiffre Bacon."""
        # Choisir la table selon la variante
        encode_table = self.encode_table_26 if self.variant == "26" else self.encode_table_24

        result = []
        for char in text:
            if char.upper() in encode_table:
                encoded = encode_table[char.upper()]
                # Appliquer les symboles personnalisés
                encoded = self._from_canonical_ab(encoded)
                result.append(encoded)
            else:
                # Conserver les caractères non alphabétiques
                result.append(char)

        return " ".join(result)

    def decode(self, text: str) -> str:
        """Décode le texte du chiffre Bacon."""
        # Choisir la table selon la variante
        encode_table = self.encode_table_26 if self.variant == "26" else self.encode_table_24
        decode_table = self._build_decode_table(encode_table)

        # Convertir en A/B canonique
        canonical = self._to_canonical_ab(text)

        # Extraire uniquement les A et B
        ab_only = ''.join(c for c in canonical if c in 'AB')

        # Découper en groupes de 5
        result = []
        for i in range(0, len(ab_only), 5):
            group = ab_only[i:i+5]
            if len(group) == 5 and group in decode_table:
                result.append(decode_table[group])
            elif group:
                result.append('?')  # Groupe incomplet ou invalide

        return ''.join(result)

    def _detect_symbols(self, text: str) -> Tuple[str, str]:
        """Détecte automatiquement les deux symboles utilisés dans le texte."""
        # Supprimer les séparateurs
        clean = text
        for sep in self.default_separators:
            clean = clean.replace(sep, "")

        # Trouver les caractères uniques
        unique_chars = list(dict.fromkeys(clean))

        if len(unique_chars) == 2:
            return unique_chars[0], unique_chars[1]

        return "A", "B"

    def execute(self, inputs: Dict) -> Dict:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        # Récupérer les paramètres
        text = inputs.get("text", "")
        mode = inputs.get("mode", "decode").lower()
        self.variant = str(inputs.get("variant", "26"))
        self.symbol_a = str(inputs.get("symbol_a", "A")) or "A"
        self.symbol_b = str(inputs.get("symbol_b", "B")) or "B"
        auto_detect = inputs.get("auto_detect_symbols", True)

        # Vérifier que les symboles sont différents
        if self.symbol_a == self.symbol_b:
            return {
                "status": "error",
                "plugin_info": {
                    "name": self.name,
                    "version": "1.0.0",
                    "execution_time_ms": 0
                },
                "results": [],
                "summary": "Les symboles A et B doivent être distincts."
            }

        # Auto-détection des symboles pour le décodage
        if mode in ("decode", "detect") and auto_detect:
            detected_a, detected_b = self._detect_symbols(text)
            if detected_a != "A" or detected_b != "B":
                self.symbol_a, self.symbol_b = detected_a, detected_b

        results = []

        if mode == "encode":
            encoded = self.encode(text)
            results.append({
                "id": "result_1",
                "text_output": encoded,
                "confidence": 1.0,
                "parameters": {
                    "mode": "encode",
                    "variant": self.variant,
                    "symbol_a": self.symbol_a,
                    "symbol_b": self.symbol_b
                },
                "metadata": {}
            })

        elif mode in ("decode", "detect"):
            decoded = self.decode(text)
            results.append({
                "id": "result_1",
                "text_output": decoded,
                "confidence": 0.8,
                "parameters": {
                    "mode": "decode",
                    "variant": self.variant,
                    "symbol_a": self.symbol_a,
                    "symbol_b": self.symbol_b
                },
                "metadata": {
                    "auto_detected": auto_detect
                }
            })

        execution_time = int((time.time() - start_time) * 1000)

        return {
            "status": "ok",
            "plugin_info": {
                "name": self.name,
                "version": "1.0.0",
                "execution_time_ms": execution_time
            },
            "results": results,
            "summary": f"{len(results)} résultat(s) généré(s)"
        }


def execute(inputs: dict) -> dict:
    """Point d'entrée pour le système de plugins."""
    plugin = BaconCodePlugin()
    return plugin.execute(inputs)
