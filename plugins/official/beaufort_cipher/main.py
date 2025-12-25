"""Plugin Beaufort Cipher pour MysterAI.

Le chiffre de Beaufort est un chiffre polyalphabétique symétrique.
La même transformation permet d'encoder et de décoder.
"""

from __future__ import annotations

import string
import time
from typing import Any, Dict, List, Tuple


class BeaufortCipherPlugin:
    """Chiffrement Beaufort (symétrique) sur l'alphabet A-Z."""

    def __init__(self) -> None:
        self.name = "beaufort_cipher"
        self.version = "1.0.0"
        self._alphabet = string.ascii_uppercase
        self._alphabet_len = 26

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()
        key = str(inputs.get("key", ""))
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        embedded = bool(inputs.get("embedded", False))
        allowed_chars = inputs.get("allowed_chars", "")
        if allowed_chars is None:
            allowed_chars = ""

        if not text:
            return self._error_response("Aucun texte fourni", start_time)

        clean_key = self._clean_key(key)
        if not clean_key:
            return self._error_response("La clé doit contenir au moins une lettre parmi A-Z", start_time)

        if mode == "detect":
            is_match, score = self._detect(text, strict_mode=strict_mode, allowed_chars=str(allowed_chars), embedded=embedded)
            return {
                "status": "ok",
                "summary": "Code Beaufort détecté" if is_match else "Aucun code Beaufort détecté",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": f"Probabilité Beaufort: {score:.2%}",
                        "confidence": float(score),
                        "parameters": {"mode": "detect", "strict": "strict" if strict_mode else "smooth", "embedded": embedded},
                        "metadata": {"is_match": is_match, "detection_score": float(score)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if strict_mode and not embedded:
            if not self._is_text_strictly_compatible(text, allowed_chars=str(allowed_chars)):
                return self._error_response("Texte incompatible avec Beaufort en mode strict", start_time)

        output, processed = self._beaufort_transform(text, key=clean_key)

        if mode == "encode":
            confidence = 1.0
            summary = "Encodage Beaufort réussi"
        elif mode == "decode":
            confidence = 0.5
            summary = "Décodage Beaufort réussi"
        else:
            return self._error_response(f"Mode inconnu: {mode}", start_time)

        return {
            "status": "ok",
            "summary": summary,
            "results": [
                {
                    "id": "result_1",
                    "text_output": output,
                    "confidence": confidence,
                    "parameters": {"mode": mode, "key": clean_key},
                    "metadata": {"processed_chars": processed},
                }
            ],
            "plugin_info": self._get_plugin_info(start_time),
        }

    def _clean_key(self, key: str) -> str:
        return "".join([c for c in key.upper() if c in self._alphabet])

    def _full_key(self, cleaned_key: str, letters_count: int) -> str:
        return (cleaned_key * (letters_count // len(cleaned_key) + 1))[:letters_count]

    def _beaufort_transform(self, text: str, key: str) -> Tuple[str, int]:
        letters_count = sum(1 for c in text.upper() if c in self._alphabet)
        full_key = self._full_key(key, letters_count)

        result: List[str] = []
        key_index = 0
        processed = 0

        for ch in text:
            up = ch.upper()
            if up in self._alphabet:
                k_val = ord(full_key[key_index]) - ord("A")
                t_val = ord(up) - ord("A")
                c_val = (k_val - t_val) % self._alphabet_len
                result.append(chr(c_val + ord("A")))
                key_index += 1
                processed += 1
            else:
                result.append(ch)

        return "".join(result), processed

    def _is_text_strictly_compatible(self, text: str, allowed_chars: str) -> bool:
        allowed_set = set(allowed_chars)
        has_letter = False
        for ch in text:
            up = ch.upper()
            if up in self._alphabet:
                has_letter = True
                continue
            if ch in allowed_set:
                continue
            return False
        return has_letter

    def _detect(self, text: str, strict_mode: bool, allowed_chars: str, embedded: bool) -> Tuple[bool, float]:
        _ = embedded

        letters = sum(1 for c in text.upper() if c in self._alphabet)
        if letters == 0:
            return False, 0.0

        if strict_mode:
            if not self._is_text_strictly_compatible(text, allowed_chars=allowed_chars):
                return False, 0.0
            total = len(text)
            score = letters / total if total else 0.0
            return True, float(score)

        allowed_set = set(allowed_chars)
        non_allowed = 0
        for ch in text:
            up = ch.upper()
            if up in self._alphabet:
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
