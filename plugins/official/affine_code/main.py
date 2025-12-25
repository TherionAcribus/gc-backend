"""Plugin Affine Code pour MysterAI.

Ce plugin implémente le chiffrement affine classique sur l'alphabet latin (A-Z).
Il supporte :
- L'encodage et le décodage via paramètres (a, b)
- Le mode brute-force (test de toutes les clés valides)

L'UI du Plugin Executor peut activer le brute-force via l'input booléen `brute_force`.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


class AffineCodePlugin:
    """Plugin de chiffrement/déchiffrement Affine.

    Args:
        inputs (dict):
            - text (str): Texte à traiter
            - mode (str): 'encode' ou 'decode'
            - a (int): Coefficient multiplicatif (doit être premier avec 26)
            - b (int): Décalage additif (0..25)
            - brute_force (bool, optionnel): Active le brute-force (uniquement en decode)

    Returns:
        dict: Résultat au format standardisé attendu par le PluginManager.
    """

    def __init__(self) -> None:
        self.name = "affine_code"
        self.version = "1.1.0"
        self._alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        self._alphabet_len = 26
        self._possible_a = [1, 3, 5, 7, 9, 11, 15, 17, 19, 21, 23, 25]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()
        brute_force = bool(inputs.get("brute_force", False))

        if not text:
            return self._error_response("Aucun texte fourni", start_time)

        try:
            a = int(inputs.get("a", 1))
            b = int(inputs.get("b", 0))
        except (TypeError, ValueError):
            return self._error_response("Paramètres a/b invalides", start_time)

        if brute_force and mode == "decode":
            results = self._bruteforce_decode(text)
            return {
                "status": "ok",
                "summary": f"Bruteforce Affine: {len(results)} combinaisons testées",
                "results": results,
                "plugin_info": self._get_plugin_info(start_time),
            }

        if a not in self._possible_a:
            return self._error_response("Le coefficient a doit être premier avec 26", start_time)

        if b < 0 or b >= self._alphabet_len:
            return self._error_response("Le coefficient b doit être entre 0 et 25", start_time)

        if mode == "encode":
            output = self._encode(text, a, b)
            return {
                "status": "ok",
                "summary": f"Encodage Affine avec a={a}, b={b} réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 1.0,
                        "parameters": {"mode": "encode", "a": a, "b": b},
                        "metadata": {"processed_chars": self._count_alpha_chars(text)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "decode":
            try:
                output = self._decode(text, a, b)
            except ValueError as exc:
                return self._error_response(str(exc), start_time)

            return {
                "status": "ok",
                "summary": f"Décodage Affine avec a={a}, b={b} réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 0.5,
                        "parameters": {"mode": "decode", "a": a, "b": b},
                        "metadata": {"processed_chars": self._count_alpha_chars(text)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        return self._error_response(f"Mode inconnu: {mode}", start_time)

    def _encode(self, text: str, a: int, b: int) -> str:
        result: List[str] = []
        for ch in text.upper():
            if ch in self._alphabet:
                x = self._alphabet.index(ch)
                y = (a * x + b) % self._alphabet_len
                result.append(self._alphabet[y])
            else:
                result.append(ch)
        return "".join(result)

    def _decode(self, text: str, a: int, b: int) -> str:
        a_inv = self._mod_inverse(a, self._alphabet_len)

        result: List[str] = []
        for ch in text.upper():
            if ch in self._alphabet:
                y = self._alphabet.index(ch)
                x = (a_inv * (y - b)) % self._alphabet_len
                result.append(self._alphabet[x])
            else:
                result.append(ch)
        return "".join(result)

    def _bruteforce_decode(self, text: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        idx = 1
        for a in self._possible_a:
            for b in range(self._alphabet_len):
                try:
                    decoded = self._decode(text, a, b)
                except ValueError:
                    continue

                results.append(
                    {
                        "id": f"result_{idx}",
                        "text_output": decoded,
                        "confidence": self._calculate_confidence(a, b),
                        "parameters": {"mode": "decode", "a": a, "b": b},
                        "metadata": {
                            "bruteforce_position": idx,
                            "processed_chars": self._count_alpha_chars(text),
                        },
                    }
                )
                idx += 1

        results.sort(key=lambda r: r.get("confidence", 0.0), reverse=True)
        return results

    def _calculate_confidence(self, a: int, b: int) -> float:
        if a == 1:
            if b in {1, 3, 13}:
                return 0.9
            return 0.8
        base = 0.6
        modifier = -0.01 * (a + b)
        return max(0.3, base + modifier)

    def _mod_inverse(self, a: int, m: int) -> int:
        a = a % m
        for x in range(1, m):
            if (a * x) % m == 1:
                return x
        raise ValueError(f"Aucun inverse modulaire n'existe pour a={a} mod m={m}")

    def _count_alpha_chars(self, text: str) -> int:
        return sum(1 for c in text.upper() if c in self._alphabet)

    def _get_plugin_info(self, start_time: float) -> Dict[str, Any]:
        execution_time = (time.time() - start_time) * 1000
        return {
            "name": self.name,
            "version": self.version,
            "execution_time_ms": round(execution_time, 2),
        }

    def _error_response(self, message: str, start_time: float) -> Dict[str, Any]:
        return {
            "status": "error",
            "summary": message,
            "results": [],
            "plugin_info": self._get_plugin_info(start_time),
        }
