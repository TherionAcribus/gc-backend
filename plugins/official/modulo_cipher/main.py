from __future__ import annotations

import random
import re
import string
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    from gc_backend.plugins.scoring import score_text_fast

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text_fast = None
    _SCORING_AVAILABLE = False


class ModuloCipherPlugin:
    def __init__(self) -> None:
        self.name = "modulo_cipher"
        self.version = "1.0.0"
        self._alphabet = string.ascii_uppercase
        self._common_modulos = [26, 27, 36, 37, 128, 256]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()
        alphabet_mapping = str(inputs.get("alphabet_mapping", "A1Z26"))

        modulo = self._coerce_int(inputs.get("modulo", 26), default=26)
        if modulo is None:
            return self._error_response("Le modulo doit être un nombre", start_time)

        brute_force = self._parse_bool(inputs.get("brute_force", False))

        if not isinstance(text, str) or not text.strip():
            return self._error_response("Aucun texte fourni", start_time)

        if modulo <= 0:
            return self._error_response("Le modulo doit être un entier positif", start_time)

        if alphabet_mapping not in {"A1Z26", "A0Z25"}:
            return self._error_response("alphabet_mapping doit être A1Z26 ou A0Z25", start_time)

        if brute_force and mode == "decode":
            results = self._bruteforce_decode(text=text, alphabet_mapping=alphabet_mapping)
            if not results:
                return self._error_response("Aucune solution trouvée en brute force", start_time)
            return {
                "status": "ok",
                "summary": f"Bruteforce Modulo: {len(results)} modulo(s) testé(s)",
                "results": results,
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "encode":
            output = self._encode(text=text, modulo=modulo, alphabet_mapping=alphabet_mapping)
            return {
                "status": "ok",
                "summary": "Encodage modulo réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 1.0,
                        "parameters": {"mode": "encode", "modulo": modulo, "alphabet_mapping": alphabet_mapping},
                        "metadata": {
                            "input_chars": len(text),
                            "output_numbers": len(self._parse_numbers_from_text(output)) if output else 0,
                        },
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "decode":
            decoded, error = self._decode(text=text, modulo=modulo, alphabet_mapping=alphabet_mapping)
            if error is not None:
                return self._error_response(error, start_time)

            confidence = self._calculate_confidence(modulo=modulo, decoded_text=decoded)
            return {
                "status": "ok",
                "summary": "Décodage modulo réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": decoded,
                        "confidence": confidence,
                        "parameters": {"mode": "decode", "modulo": modulo, "alphabet_mapping": alphabet_mapping},
                        "metadata": {
                            "input_numbers": len(self._parse_numbers_from_text(text)),
                            "decoded_chars": len(decoded),
                        },
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "detect":
            is_match, score, parsed_count = self._detect(text=text, modulo=modulo)
            summary = "Code modulo détecté" if is_match else "Aucun code modulo détecté"
            return {
                "status": "ok",
                "summary": summary,
                "results": [
                    {
                        "id": "result_1",
                        "text_output": f"{summary} (score: {score:.2f})",
                        "confidence": float(score),
                        "parameters": {"mode": "detect", "modulo": modulo},
                        "metadata": {"is_match": is_match, "detection_score": float(score), "numbers": parsed_count},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        return self._error_response(f"Mode inconnu: {mode}", start_time)

    def _encode(self, text: str, modulo: int, alphabet_mapping: str) -> str:
        numbers = self._text_to_numbers(text=text, alphabet_mapping=alphabet_mapping)
        encoded_numbers: List[int] = []
        for num in numbers:
            multiplier = random.randint(1, 50)
            encoded_numbers.append(multiplier * modulo + num)
        return ",".join(map(str, encoded_numbers))

    def _decode(self, text: str, modulo: int, alphabet_mapping: str) -> Tuple[str, Optional[str]]:
        numbers = self._parse_numbers_from_text(text)
        if not numbers:
            return "", "Aucun nombre trouvé dans le texte"

        decoded_numbers = [n % modulo for n in numbers]
        decoded_text = self._numbers_to_text(numbers=decoded_numbers, alphabet_mapping=alphabet_mapping)

        if "?" in decoded_text:
            return decoded_text, "Décodage partiel: certains codes sont hors plage (?). Vérifiez modulo/mapping."

        return decoded_text, None

    @staticmethod
    def _get_score_fast(text: str) -> float:
        if not _SCORING_AVAILABLE or not score_text_fast:
            return 0.0
        try:
            return score_text_fast(text)
        except Exception:
            return 0.0

    def _bruteforce_decode(self, text: str, alphabet_mapping: str) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        idx = 1

        for modulo in self._common_modulos:
            decoded, error = self._decode(text=text, modulo=modulo, alphabet_mapping=alphabet_mapping)
            if error is not None and decoded == "":
                continue

            confidence = self._calculate_confidence(modulo=modulo, decoded_text=decoded)
            fast = self._get_score_fast(decoded)
            if fast > 0:
                confidence = max(confidence, fast)
            results.append(
                {
                    "id": f"result_{idx}",
                    "text_output": decoded,
                    "confidence": confidence,
                    "parameters": {"mode": "decode", "modulo": modulo, "alphabet_mapping": alphabet_mapping},
                    "metadata": {
                        "bruteforce": True,
                        "bruteforce_position": idx,
                        "warning": error,
                    },
                }
            )
            idx += 1

        results.sort(key=lambda r: float(r.get("confidence", 0.0) or 0.0), reverse=True)
        return results

    def _detect(self, text: str, modulo: int) -> Tuple[bool, float, int]:
        numbers = self._parse_numbers_from_text(text)
        if not numbers:
            return False, 0.0, 0

        remainders = [n % modulo for n in numbers]

        # Heuristique: plus il y a de valeurs dans une plage plausible (0..26), plus c'est crédible.
        plausible = sum(1 for r in remainders if 0 <= r <= 26)
        score = plausible / len(remainders) if remainders else 0.0
        return score >= 0.6, float(score), len(numbers)

    def _text_to_numbers(self, text: str, alphabet_mapping: str) -> List[int]:
        conversion = self._char_to_num_a1z26 if alphabet_mapping == "A1Z26" else self._char_to_num_a0z25
        numbers: List[int] = []
        for ch in text.upper():
            if ch in self._alphabet:
                numbers.append(conversion(ch))
        return numbers

    def _numbers_to_text(self, numbers: List[int], alphabet_mapping: str) -> str:
        conversion = self._num_to_char_a1z26 if alphabet_mapping == "A1Z26" else self._num_to_char_a0z25
        return "".join(conversion(n) for n in numbers)

    def _char_to_num_a1z26(self, char: str) -> int:
        return ord(char.upper()) - ord("A") + 1

    def _char_to_num_a0z25(self, char: str) -> int:
        return ord(char.upper()) - ord("A")

    def _num_to_char_a1z26(self, num: int) -> str:
        if 1 <= num <= 26:
            return chr(ord("A") + num - 1)
        return "?"

    def _num_to_char_a0z25(self, num: int) -> str:
        if 0 <= num <= 25:
            return chr(ord("A") + num)
        return "?"

    def _parse_numbers_from_text(self, text: str) -> List[int]:
        s = text.strip()
        if not s:
            return []

        # Common separators first
        if "," in s:
            return self._safe_int_list([t.strip() for t in s.split(",")])
        if "-" in s:
            return self._safe_int_list([t.strip() for t in s.split("-")])

        # If spaces are present and it's not purely digits, split on whitespace
        if re.search(r"\s", s) and not s.isdigit():
            return self._safe_int_list(re.split(r"\s+", s))

        # Pure digits: try groups (3, then 2) else whole number
        if s.isdigit() and len(s) > 2:
            if len(s) % 3 == 0:
                return [int(s[i : i + 3]) for i in range(0, len(s), 3)]
            if len(s) % 2 == 0:
                return [int(s[i : i + 2]) for i in range(0, len(s), 2)]
            return [int(s)]

        if s.isdigit():
            return [int(s)]

        # Fallback: find any digits sequences
        return [int(m.group(0)) for m in re.finditer(r"\d+", s)]

    def _safe_int_list(self, tokens: List[str]) -> List[int]:
        out: List[int] = []
        for tok in tokens:
            if not tok:
                continue
            if tok.isdigit():
                out.append(int(tok))
                continue
            m = re.match(r"^\d+$", tok)
            if m:
                out.append(int(m.group(0)))
        return out

    def _calculate_confidence(self, modulo: int, decoded_text: str) -> float:
        if not decoded_text:
            return 0.1

        if "?" in decoded_text:
            return 0.1

        if modulo == 26:
            base = 0.9
        elif modulo == 27:
            base = 0.8
        elif modulo in {36, 37}:
            base = 0.7
        else:
            base = 0.6

        valid = sum(1 for c in decoded_text if c in self._alphabet)
        ratio = valid / len(decoded_text) if decoded_text else 0.0
        return max(0.1, min(1.0, base * ratio))

    def _parse_bool(self, value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on"}
        return default

    def _coerce_int(self, value: Any, default: Optional[int] = None) -> Optional[int]:
        if value is None:
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            if not value.strip():
                return default
            try:
                return int(value)
            except ValueError:
                return None
        return None

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
    return ModuloCipherPlugin().execute(inputs)
