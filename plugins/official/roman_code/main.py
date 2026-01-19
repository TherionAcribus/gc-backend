from __future__ import annotations

import re
import time
from typing import Any, Dict, List


class RomanCodePlugin:
    def __init__(self) -> None:
        self.name = "roman_code"
        self.version = "1.0.0"
        self.description = "Convertit un entier décimal en chiffres romains et inversement."

        try:
            from gc_backend.plugins.scoring import score_text

            self._score_text = score_text
            self._scoring_available = True
        except Exception:
            self._score_text = None
            self._scoring_available = False

    def encode_roman(self, number: int) -> str:
        if number <= 0:
            raise ValueError("Les chiffres romains ne sont pas définis pour 0 ou négatifs.")

        roman_map = [
            (1000, "M"),
            (900, "CM"),
            (500, "D"),
            (400, "CD"),
            (100, "C"),
            (90, "XC"),
            (50, "L"),
            (40, "XL"),
            (10, "X"),
            (9, "IX"),
            (5, "V"),
            (4, "IV"),
            (1, "I"),
        ]

        result = []
        for val, symb in roman_map:
            while number >= val:
                result.append(symb)
                number -= val
        return "".join(result)

    def decode_roman(self, roman_str: str) -> int:
        roman_values = {
            "M": 1000,
            "D": 500,
            "C": 100,
            "L": 50,
            "X": 10,
            "V": 5,
            "I": 1,
        }

        roman_str = roman_str.upper()
        total = 0
        prev_value = 0

        for char in reversed(roman_str):
            if char not in roman_values:
                raise ValueError(f"Symbole romain inconnu: {char}")
            value = roman_values[char]
            if value >= prev_value:
                total += value
            else:
                total -= value
            prev_value = value

        return total

    def _extract_roman_fragments(self, text: str, allowed_chars: str) -> dict:
        roman_chars = "IVXLCDM"
        esc_punct = re.escape(allowed_chars)
        pattern = f"([^{esc_punct}]+)|([{esc_punct}]+)"
        fragments = []

        for match in re.finditer(pattern, text.upper()):
            block = match.group(0)
            start, end = match.span()
            if re.match(f"^[{esc_punct}]+$", block):
                continue
            if all(char in roman_chars for char in block):
                try:
                    self.decode_roman(block)
                    fragments.append({"value": text[start:end], "start": start, "end": end})
                except ValueError:
                    continue

        score = 1.0 if fragments else 0.0
        return {"is_match": bool(fragments), "fragments": fragments, "score": score}

    def check_code(self, text: str, strict: bool = False, allowed_chars: str | None = None, embedded: bool = False) -> dict:
        if allowed_chars is not None and isinstance(allowed_chars, list):
            allowed_chars = "".join(allowed_chars)
        # Normalize empty/None to default set to avoid invalid regex like "[]".
        if not allowed_chars:
            allowed_chars = " \t\r\n.:;,_-°"

        roman_chars = "IVXLCDM"

        if strict:
            if embedded:
                return self._extract_roman_fragments(text, allowed_chars)

            esc_punct = re.escape(allowed_chars)
            pattern_str = f"^[{roman_chars}{esc_punct}]*$"
            if not re.match(pattern_str, text.upper()):
                return {"is_match": False, "fragments": [], "score": 0.0}

            roman_chars_found = re.sub(f"[{esc_punct}]", "", text.upper())
            if not roman_chars_found:
                return {"is_match": False, "fragments": [], "score": 0.0}

            clean_text = re.sub(f"[{esc_punct}]", "", text.upper())
            try:
                self.decode_roman(clean_text)
            except ValueError:
                return {"is_match": False, "fragments": [], "score": 0.0}

            stripped_text = text.strip(allowed_chars)
            start_index = text.find(stripped_text)
            return {
                "is_match": True,
                "fragments": [
                    {
                        "value": stripped_text,
                        "start": start_index,
                        "end": start_index + len(stripped_text),
                    }
                ],
                "score": 1.0,
            }

        return self._extract_roman_fragments(text, allowed_chars)

    def decode_fragments(self, text: str, fragments: List[Dict[str, Any]]) -> str:
        result = list(text)
        for fragment in sorted(fragments, key=lambda frag: frag["start"], reverse=True):
            start = fragment["start"]
            end = fragment["end"]
            value = fragment["value"]
            try:
                decoded = str(self.decode_roman(value))
                result[start:end] = decoded
            except ValueError:
                continue
        return "".join(result)

    def _get_text_score(self, text: str, context: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
        if not self._scoring_available or not self._score_text:
            return None
        try:
            return self._score_text(text, context=context or {})
        except Exception:
            return None

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "encode")).lower()
        text = inputs.get("text", inputs.get("value", ""))
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        allowed_chars = inputs.get("allowed_chars")
        embedded = self._is_truthy(inputs.get("embedded", False))
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))
        context = inputs.get("context", {})

        standardized_response = {
            "status": "success",
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time": 0,
            },
            "inputs": inputs.copy(),
            "results": [],
            "summary": {
                "best_result_id": None,
                "total_results": 0,
                "message": "",
            },
        }

        if not text:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = "Aucun texte fourni à traiter."
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        try:
            if mode == "encode":
                try:
                    number = int(text)
                except (TypeError, ValueError):
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = (
                        "Le texte doit être un nombre entier pour l'encodage en chiffres romains."
                    )
                    return standardized_response

                if number <= 0:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = (
                        "Le nombre doit être positif pour l'encodage en chiffres romains."
                    )
                    return standardized_response

                encoded = self.encode_roman(number)
                standardized_response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": encoded,
                        "confidence": 1.0,
                        "parameters": {
                            "mode": "encode",
                        },
                        "metadata": {
                            "input_number": number,
                        },
                    }
                )

                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": f"Encodage réussi: {number} => {encoded}",
                    }
                )

            elif mode == "decode":
                check = self.check_code(text, strict=strict_mode, allowed_chars=allowed_chars, embedded=embedded)
                if not check["is_match"]:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = (
                        "Chiffres romains invalides en mode strict"
                        if strict_mode
                        else "Aucun chiffre romain détecté dans le texte"
                    )
                    return standardized_response

                decoded = self.decode_fragments(text, check["fragments"])
                if decoded == text:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = "Aucun chiffre romain n'a pu être décodé"
                    return standardized_response

                confidence = 0.8
                scoring_info = None
                if enable_scoring:
                    scoring_info = self._get_text_score(decoded, context)
                    if scoring_info and "score" in scoring_info:
                        confidence = float(scoring_info["score"])

                result = {
                    "id": "result_1",
                    "text_output": decoded,
                    "confidence": confidence,
                    "parameters": {
                        "mode": "decode",
                        "strict": "strict" if strict_mode else "smooth",
                        "embedded": embedded,
                    },
                    "metadata": {
                        "fragments_count": len(check["fragments"]),
                        "fragments": [frag["value"] for frag in check["fragments"]],
                    },
                }
                if scoring_info:
                    result["scoring"] = scoring_info

                standardized_response["results"].append(result)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Décodage réussi",
                    }
                )

            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode inconnu : {mode}"

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur pendant le traitement : {exc}"

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return RomanCodePlugin().execute(inputs)
