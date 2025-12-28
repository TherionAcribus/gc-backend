from __future__ import annotations

import time
from typing import Any, Dict, List


class MultiplicativeCodePlugin:
    """Chiffrement multiplicatif : E(x) = (a * x) mod 26."""

    def __init__(self) -> None:
        self.name = "multiplicative_code"
        self.version = "1.0.0"
        self._alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        self._alphabet_len = 26
        self._possible_a = [1, 3, 5, 7, 9, 11, 15, 17, 19, 21, 23, 25]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()
        brute_force = bool(inputs.get("brute_force", False))

        if not isinstance(text, str) or not text.strip():
            return self._error_response("Aucun texte fourni", start_time)

        try:
            a = int(inputs.get("a", 1))
        except (TypeError, ValueError):
            return self._error_response("Coefficient a invalide", start_time)

        if a not in self._possible_a:
            return self._error_response("Le coefficient a doit être premier avec 26", start_time)

        if brute_force and mode == "decode":
            results = self._bruteforce_decode(text)
            if not results:
                return self._error_response("Aucune solution trouvée en bruteforce", start_time)
            return {
                "status": "ok",
                "summary": f"Bruteforce multiplicatif: {len(results)} solution(s)",
                "results": results,
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "encode":
            output = self._encode(text, a)
            return {
                "status": "ok",
                "summary": f"Encodage multiplicatif avec a={a} réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 1.0,
                        "parameters": {"mode": "encode", "a": a},
                        "metadata": {"processed_chars": self._count_alpha(text)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "decode":
            try:
                output = self._decode(text, a)
            except ValueError as exc:
                return self._error_response(str(exc), start_time)

            return {
                "status": "ok",
                "summary": f"Décodage multiplicatif avec a={a} réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 0.6,
                        "parameters": {"mode": "decode", "a": a},
                        "metadata": {"processed_chars": self._count_alpha(text)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "detect":
            is_match, score = self._detect(text)
            summary = "Code multiplicatif détecté" if is_match else "Aucun code multiplicatif détecté"
            return {
                "status": "ok",
                "summary": summary,
                "results": [
                    {
                        "id": "result_1",
                        "text_output": f"{summary} (score: {score:.2f})",
                        "confidence": float(score),
                        "parameters": {"mode": "detect"},
                        "metadata": {"is_match": is_match, "detection_score": float(score)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        return self._error_response(f"Mode inconnu: {mode}", start_time)

    def _encode(self, text: str, a: int) -> str:
        out: List[str] = []
        for ch in text.upper():
            if ch in self._alphabet:
                x = self._alphabet.index(ch)
                y = (a * x) % self._alphabet_len
                out.append(self._alphabet[y])
            else:
                out.append(ch)
        return "".join(out)

    def _decode(self, text: str, a: int) -> str:
        a_inv = self._mod_inverse(a, self._alphabet_len)
        out: List[str] = []
        for ch in text.upper():
            if ch in self._alphabet:
                y = self._alphabet.index(ch)
                x = (a_inv * y) % self._alphabet_len
                out.append(self._alphabet[x])
            else:
                out.append(ch)
        return "".join(out)

    def _bruteforce_decode(self, text: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for idx, a in enumerate(self._possible_a, 1):
            try:
                decoded = self._decode(text, a)
            except ValueError:
                continue
            results.append(
                {
                    "id": f"result_{idx}",
                    "text_output": decoded,
                    "confidence": self._confidence(a),
                    "parameters": {"mode": "decode", "a": a},
                    "metadata": {"bruteforce_position": idx, "processed_chars": self._count_alpha(text)},
                }
            )
        results.sort(key=lambda r: float(r.get("confidence", 0.0)), reverse=True)
        return results

    def _detect(self, text: str) -> tuple[bool, float]:
        letters = self._count_alpha(text)
        if letters == 0:
            return False, 0.0
        # Heuristique simple : s'il y a beaucoup de lettres A-Z, on considère que ça peut être un code substitutif.
        total = len(text.strip()) or 1
        score = min(1.0, letters / total)
        return score >= 0.3, float(score)

    def _confidence(self, a: int) -> float:
        if a in {1, 3, 5}:
            return 0.9
        return 0.7

    def _count_alpha(self, text: str) -> int:
        return sum(1 for c in text.upper() if c in self._alphabet)

    def _mod_inverse(self, a: int, m: int) -> int:
        a = a % m
        for x in range(1, m):
            if (a * x) % m == 1:
                return x
        raise ValueError(f"Aucun inverse modulaire pour a={a} mod {m}")

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


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return MultiplicativeCodePlugin().execute(inputs)
