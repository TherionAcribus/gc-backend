"""Plugin Gold-Bug (Scarabée d'or) pour MysterAI.

Substitution simple A-Z -> symboles (et inverse).
"""

from __future__ import annotations

import string
import time
from typing import Any, Dict, Tuple


class GoldBugPlugin:
    def __init__(self) -> None:
        self.name = "gold_bug"
        self.version = "1.0.0"

        self.encode_table: Dict[str, str] = {
            "A": "5",
            "B": "2",
            "C": "-",
            "D": "†",
            "E": "8",
            "F": "1",
            "G": "3",
            "H": "4",
            "I": "6",
            "J": ",",
            "K": "7",
            "L": "0",
            "M": "9",
            "N": "*",
            "O": "‡",
            "P": ".",
            "Q": "$",
            "R": "(",
            "S": ")",
            "T": ";",
            "U": "?",
            "V": "¶",
            "W": "]",
            "X": "¢",
            "Y": ":",
            "Z": "[",
        }
        # Inverse : symbole -> lettre
        self.decode_table: Dict[str, str] = {v: k for k, v in self.encode_table.items()}

        self._alphabet = set(string.ascii_uppercase)

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")

        if not isinstance(text, str) or text == "":
            return self._error_response("Aucun texte fourni", start_time)

        try:
            if mode == "encode":
                output = self.encode(text)
                return {
                    "status": "ok",
                    "summary": "Encodage Gold-Bug réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": output,
                            "confidence": 1.0,
                            "parameters": {"mode": "encode"},
                            "metadata": {"processed_chars": len(text)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "decode":
                output = self.decode(text)
                return {
                    "status": "ok",
                    "summary": "Décodage Gold-Bug réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": output,
                            "confidence": 0.5,
                            "parameters": {"mode": "decode"},
                            "metadata": {"processed_chars": len(text)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "detect":
                is_match, score = self.detect(text)
                return {
                    "status": "ok",
                    "summary": "Code Gold-Bug détecté" if is_match else "Aucun code Gold-Bug détecté",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": f"Probabilité Gold-Bug: {score:.2%}",
                            "confidence": float(score),
                            "parameters": {"mode": "detect"},
                            "metadata": {"is_match": is_match, "detection_score": float(score)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            return self._error_response(f"Mode inconnu: {mode}", start_time)

        except Exception as e:
            return self._error_response(str(e), start_time)

    def encode(self, text: str) -> str:
        out = []
        for ch in text.upper():
            if ch in self.encode_table:
                out.append(self.encode_table[ch])
            else:
                out.append(ch)
        return "".join(out)

    def decode(self, text: str) -> str:
        out = []
        for ch in text:
            if ch in self.decode_table:
                out.append(self.decode_table[ch])
            else:
                out.append(ch)
        return "".join(out)

    def detect(self, text: str) -> Tuple[bool, float]:
        # Score basé sur la proportion de symboles Gold-Bug présents.
        non_space = [c for c in text if not c.isspace()]
        if not non_space:
            return False, 0.0

        symbol_hits = sum(1 for c in non_space if c in self.decode_table)
        letters = sum(1 for c in non_space if c.upper() in self._alphabet)

        # Si le texte est principalement des symboles de la table : score élevé.
        score = symbol_hits / len(non_space)

        # Si on n'a que des lettres, c'est probablement un texte clair (peu utile pour detect)
        if symbol_hits == 0 and letters > 0:
            return False, 0.0

        return score >= 0.25, float(score)

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
    return GoldBugPlugin().execute(inputs)
