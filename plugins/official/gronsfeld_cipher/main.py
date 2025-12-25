"""Plugin Gronsfeld Cipher pour MysterAI.

Le chiffre de Gronsfeld est une variante de Vigenère utilisant une clé numérique.
Chaque lettre est décalée selon le chiffre correspondant (0-9) de la clé.
"""

from __future__ import annotations

import string
import time
from typing import Any, Dict, List


class GronsfeldCipherPlugin:
    def __init__(self) -> None:
        self.name = "gronsfeld_cipher"
        self.version = "1.0.0"
        self._alphabet = string.ascii_uppercase

    @staticmethod
    def _clean_key(key: str) -> str:
        return "".join(c for c in key if c.isdigit())

    def _key_digits(self, key: str) -> List[int]:
        cleaned = self._clean_key(key)
        if not cleaned:
            raise ValueError("La clé doit contenir au moins un chiffre (0-9)")
        return [int(c) for c in cleaned]

    def _validate_text_strict(self, text: str) -> None:
        for ch in text:
            if ch.upper() in self._alphabet:
                continue
            if ch in " \t\r\n.:;,_-'\"!?":
                continue
            raise ValueError(f"Caractère non autorisé en mode strict: {repr(ch)}")

    def encode(self, text: str, key: str, strict: str = "smooth") -> str:
        if strict == "strict":
            self._validate_text_strict(text)

        digits = self._key_digits(key)
        out: List[str] = []
        key_index = 0

        for ch in text:
            up = ch.upper()
            if up in self._alphabet:
                shift = digits[key_index % len(digits)]
                pos = ord(up) - ord("A")
                out.append(chr((pos + shift) % 26 + ord("A")))
                key_index += 1
            else:
                out.append(ch)

        return "".join(out)

    def decode(self, text: str, key: str, strict: str = "smooth") -> str:
        if strict == "strict":
            self._validate_text_strict(text)

        digits = self._key_digits(key)
        out: List[str] = []
        key_index = 0

        for ch in text:
            up = ch.upper()
            if up in self._alphabet:
                shift = digits[key_index % len(digits)]
                pos = ord(up) - ord("A")
                out.append(chr((pos - shift) % 26 + ord("A")))
                key_index += 1
            else:
                out.append(ch)

        return "".join(out)

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        key = inputs.get("key", "")
        strict = str(inputs.get("strict", "smooth")).lower()

        if not isinstance(text, str) or text == "":
            return self._error_response("Aucun texte fourni", start_time)
        if not isinstance(key, str) or key == "":
            return self._error_response("Aucune clé fournie", start_time)
        if strict not in {"strict", "smooth"}:
            return self._error_response(f"Mode strict invalide: {strict}", start_time)

        try:
            if mode == "encode":
                output = self.encode(text, key, strict=strict)
                confidence = 1.0
                summary = "Encodage Gronsfeld réussi"
            elif mode == "decode":
                output = self.decode(text, key, strict=strict)
                confidence = 0.5
                summary = "Décodage Gronsfeld réussi"
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
                        "parameters": {"mode": mode, "key": key, "strict": strict},
                        "metadata": {"processed_chars": len(text)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        except Exception as e:
            return self._error_response(str(e), start_time)

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
    return GronsfeldCipherPlugin().execute(inputs)
