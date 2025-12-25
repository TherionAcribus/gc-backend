"""Plugin Alpha Decoder pour MysterAI.

Ce plugin convertit :
- decode: nombres -> lettres (A=1, B=2, ..., Z=26)
- encode: lettres -> nombres

Le paramètre `offset` permet d'appliquer un décalage circulaire.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List


class AlphaDecoderPlugin:
    """Chiffreur/Déchiffreur alphabétique avec gestion du décalage."""

    def __init__(self) -> None:
        self.name = "alpha_decoder"
        self.version = "1.4.0"
        self._alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        self._alphabet_len = 26

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()

        try:
            offset = int(inputs.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0

        if not text:
            return self._error_response("Aucun texte fourni", start_time)

        if mode == "encode":
            output = self._encode_text(text, offset)
            return {
                "status": "ok",
                "summary": "Encodage alpha réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 1.0,
                        "parameters": {"mode": "encode", "offset": offset},
                        "metadata": {"processed_chars": self._count_letters(text)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "decode":
            output = self._decode_text(text, offset)
            return {
                "status": "ok",
                "summary": "Décodage alpha réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 0.5,
                        "parameters": {"mode": "decode", "offset": offset},
                        "metadata": {"processed_chars": self._count_numbers(text)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        return self._error_response(f"Mode inconnu: {mode}", start_time)

    def _encode_text(self, text: str, offset: int) -> str:
        tokens: List[str] = []

        # Encodage caractère par caractère, en conservant espaces/ponctuation.
        for idx, ch in enumerate(text.upper()):
            if ch in self._alphabet:
                pos = self._alphabet.index(ch)  # 0..25
                adjusted_pos = (pos + 1 - offset) % self._alphabet_len
                if adjusted_pos == 0:
                    adjusted_pos = 26
                tokens.append(str(adjusted_pos))

                next_char = text[idx + 1] if idx + 1 < len(text) else ""
                if next_char and next_char.isalpha():
                    tokens.append(" ")
            elif ch.isspace():
                tokens.append(ch)
            else:
                tokens.append(ch)

        return "".join(tokens).strip()

    def _decode_text(self, text: str, offset: int) -> str:
        def repl(match: re.Match[str]) -> str:
            num = int(match.group(0))
            adjusted = (num - 1 + offset) % self._alphabet_len
            return self._alphabet[adjusted]

        # Remplace chaque séquence de chiffres par une lettre, en conservant le reste.
        return re.sub(r"\d+", repl, text)

    def _count_letters(self, text: str) -> int:
        return sum(1 for c in text.upper() if c in self._alphabet)

    def _count_numbers(self, text: str) -> int:
        return len(re.findall(r"\d+", text))

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
