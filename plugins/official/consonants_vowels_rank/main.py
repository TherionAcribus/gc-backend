"""Plugin Consonants/Vowels Rank pour MysterAI.

Chiffrement/déchiffrement par rang :
- Voyelles: A=1, E=2, I=3, O=4, U=5, Y=6
- Consonnes: B=1, C=2, ... Z=20 (sans voyelles)

Variantes:
- both: préfixe C/V (ex: C3V1)
- consonants: encode uniquement les consonnes en nombres (séparés par espaces)
- vowels: encode uniquement les voyelles en nombres (séparés par espaces)
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List


class ConsonantsVowelsRankPlugin:
    VOWELS = "AEIOUY"
    CONSONANTS = "BCDFGHJKLMNPQRSTVWXZ"

    def __init__(self) -> None:
        self.name = "consonants_vowels_rank"
        self.version = "1.0.0"

        self.vowel_to_rank = {ch: idx + 1 for idx, ch in enumerate(self.VOWELS)}
        self.rank_to_vowel = {idx + 1: ch for idx, ch in enumerate(self.VOWELS)}

        self.consonant_to_rank = {ch: idx + 1 for idx, ch in enumerate(self.CONSONANTS)}
        self.rank_to_consonant = {idx + 1: ch for idx, ch in enumerate(self.CONSONANTS)}

    def encode(self, text: str, variant: str = "both") -> str:
        tokens: List[str] = []
        for ch in text.upper():
            if ch in self.VOWELS:
                rank = self.vowel_to_rank[ch]
                if variant == "both":
                    tokens.append(f"V{rank}")
                elif variant == "vowels":
                    tokens.append(str(rank))
                else:
                    tokens.append(ch)
            elif ch in self.CONSONANTS:
                rank = self.consonant_to_rank[ch]
                if variant == "both":
                    tokens.append(f"C{rank}")
                elif variant == "consonants":
                    tokens.append(str(rank))
                else:
                    tokens.append(ch)
            else:
                tokens.append(ch)

        if variant in {"consonants", "vowels"}:
            return " ".join(tokens)
        return "".join(tokens)

    def _decode_both(self, text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            prefix = match.group(1)
            num = int(match.group(2))
            if prefix.upper() == "C":
                return self.rank_to_consonant.get(num, "?")
            return self.rank_to_vowel.get(num, "?")

        pattern = re.compile(r"([CVcv])(\d{1,2})")
        return pattern.sub(repl, text)

    def _decode_single_group(self, text: str, mapping: Dict[int, str]) -> str:
        tokens = re.findall(r"\d{1,2}|\D", text)
        decoded: List[str] = []
        buffer_num = ""

        for token in tokens:
            if token.isdigit():
                buffer_num += token
                continue

            if buffer_num:
                decoded.append(mapping.get(int(buffer_num), "?"))
                buffer_num = ""
            decoded.append(token)

        if buffer_num:
            decoded.append(mapping.get(int(buffer_num), "?"))

        return "".join(decoded)

    def decode(self, text: str, variant: str = "both") -> str:
        if variant == "both":
            return self._decode_both(text)
        if variant == "consonants":
            return self._decode_single_group(text, self.rank_to_consonant)
        if variant == "vowels":
            return self._decode_single_group(text, self.rank_to_vowel)
        raise ValueError(f"Variante inconnue: {variant}")

    def _compute_basic_confidence(self, text: str) -> float:
        if not text:
            return 0.0
        letters = sum(1 for c in text if c.isalpha())
        ratio = letters / len(text)
        return max(0.3, min(0.3 + ratio * 0.6, 0.9))

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get("text", "")
        if not isinstance(text, str) or text == "":
            return self._error_response("Aucun texte fourni", start_time)

        variant = str(inputs.get("variant", "both"))
        mode = str(inputs.get("mode", "decode")).lower()

        try:
            if mode == "encode":
                out = self.encode(text, variant)
                return {
                    "status": "ok",
                    "summary": "Encodage réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": out,
                            "confidence": 1.0,
                            "parameters": {"mode": "encode", "variant": variant},
                            "metadata": {"processed_chars": len(text)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "decode":
                out = self.decode(text, variant)
                return {
                    "status": "ok",
                    "summary": "Décodage réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": out,
                            "confidence": 0.9,
                            "parameters": {"mode": "decode", "variant": variant},
                            "metadata": {"processed_chars": len(text)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "bruteforce":
                variants = ["both", "consonants", "vowels"]
                results: List[Dict[str, Any]] = []

                for idx, var in enumerate(variants, 1):
                    decoded = self.decode(text, var)
                    confidence = self._compute_basic_confidence(decoded)
                    results.append(
                        {
                            "id": f"result_{idx}",
                            "text_output": decoded,
                            "confidence": confidence,
                            "parameters": {"mode": "decode", "variant": var},
                            "metadata": {"processed_chars": len(text)},
                        }
                    )

                results.sort(key=lambda r: r["confidence"], reverse=True)

                return {
                    "status": "ok",
                    "summary": "Bruteforce terminé sur les trois variantes",
                    "results": results,
                    "plugin_info": self._get_plugin_info(start_time),
                }

            return self._error_response("Mode invalide. Utilisez 'encode', 'decode' ou 'bruteforce'.", start_time)

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
    return ConsonantsVowelsRankPlugin().execute(inputs)
