"""Plugin Atbash pour MysterAI.

Ce plugin implémente le chiffrement Atbash sur l'alphabet latin (A-Z).
Atbash est symétrique : encoder = décoder.

Le plugin supporte :
- encode / decode
- detect (détection stricte ou souple)
- strict/smooth :
  - strict: le texte ne contient que des lettres + `allowed_chars`
  - smooth: on transforme uniquement les lettres et on conserve le reste
- embedded: en mode texte mixte, on conserve les séparateurs et transforme seulement les lettres.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


class AtbashPlugin:
    """Chiffrement Atbash (A↔Z, B↔Y...)."""

    def __init__(self) -> None:
        self.name = "atbash"
        self.version = "1.0.0"
        self._alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        # Table miroir
        self._encode_table = {chr(ord('A') + i): chr(ord('Z') - i) for i in range(26)}

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        embedded = bool(inputs.get("embedded", False))
        allowed_chars = inputs.get("allowed_chars", "")
        if allowed_chars is None:
            allowed_chars = ""

        if not text:
            return self._error_response("Aucun texte fourni", start_time)

        if mode == "detect":
            is_match, score = self._detect(text, strict_mode=strict_mode, allowed_chars=str(allowed_chars), embedded=embedded)
            return {
                "status": "ok",
                "summary": "Code Atbash détecté" if is_match else "Aucun code Atbash détecté",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": f"Probabilité Atbash: {score:.2%}",
                        "confidence": float(score),
                        "parameters": {"mode": "detect", "strict": "strict" if strict_mode else "smooth", "embedded": embedded},
                        "metadata": {"is_match": is_match, "detection_score": float(score)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if strict_mode and not self._is_text_strictly_compatible(text, allowed_chars=str(allowed_chars)):
            return self._error_response("Texte incompatible avec Atbash en mode strict", start_time)

        if mode == "encode" or mode == "decode":
            output, processed = self._transform(text)
            # En embedded/smooth, on conserve déjà la ponctuation/espaces.
            return {
                "status": "ok",
                "summary": "Atbash appliqué avec succès",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 1.0 if mode == "encode" else 0.5,
                        "parameters": {"mode": mode, "strict": "strict" if strict_mode else "smooth", "embedded": embedded},
                        "metadata": {"processed_chars": processed},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        return self._error_response(f"Mode inconnu: {mode}", start_time)

    def _transform(self, text: str) -> Tuple[str, int]:
        out: List[str] = []
        processed = 0
        for ch in text:
            up = ch.upper()
            if up in self._encode_table:
                out.append(self._encode_table[up])
                processed += 1
            else:
                out.append(ch)
        return "".join(out), processed

    def _is_text_strictly_compatible(self, text: str, allowed_chars: str) -> bool:
        allowed_set = set(allowed_chars)
        has_letter = False
        for ch in text:
            up = ch.upper()
            if up in self._encode_table:
                has_letter = True
                continue
            if ch in allowed_set:
                continue
            return False
        return has_letter

    def _detect(self, text: str, strict_mode: bool, allowed_chars: str, embedded: bool) -> Tuple[bool, float]:
        # embedded n'a pas d'impact majeur sur Atbash (substitution lettre/lettre),
        # mais on conserve l'API uniforme.
        _ = embedded

        letters = sum(1 for c in text.upper() if c in self._encode_table)
        if letters == 0:
            return False, 0.0

        if strict_mode:
            if not self._is_text_strictly_compatible(text, allowed_chars=allowed_chars):
                return False, 0.0
            total = len(text)
            score = letters / total if total else 0.0
            return True, float(score)

        # smooth: score = ratio de lettres sur (lettres + chars non autorisés)
        allowed_set = set(allowed_chars)
        non_allowed = 0
        for ch in text:
            up = ch.upper()
            if up in self._encode_table:
                continue
            if ch in allowed_set:
                continue
            non_allowed += 1

        denom = letters + non_allowed
        score = letters / denom if denom else 0.0
        return True, float(score)

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
