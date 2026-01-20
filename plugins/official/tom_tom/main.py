from __future__ import annotations

import re
import time
from typing import Any, Dict, List

try:
    from gc_backend.plugins.scoring import score_text

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text = None
    _SCORING_AVAILABLE = False


class TomTomPlugin:
    def __init__(self) -> None:
        self.name = "tom_tom"
        self.version = "1.0.0"
        self.default_separators = " \t\r\n.:;,_-"

        self.encode_table = {
            "A": "/",
            "B": "//",
            "C": "///",
            "D": "////",
            "E": "/\\",
            "F": "//\\",
            "G": "///\\",
            "H": "/\\\\",
            "I": "/\\\\\\",
            "J": "\\/",
            "K": "\\\\/",
            "L": "\\\\\\/",
            "M": "\\//",
            "N": "\\///",
            "O": "/\\/",
            "P": "//\\/",
            "Q": "/\\\\/",
            "R": "/\\//",
            "S": "\\/\\",
            "T": "\\\\\\/\\",
            "U": "\\//\\",
            "V": "\\/\\\\",
            "W": "//\\\\",
            "X": "\\\\//",
            "Y": "\\/\\/",
            "Z": "/\\/\\",
        }
        self.decode_table = {value: key for key, value in self.encode_table.items()}

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    def _split_by_separators(self, text: str, separators: str) -> List[str]:
        esc_sep = re.escape(separators)
        pattern = f"[^{esc_sep}]+"
        return [match.group(0) for match in re.finditer(pattern, text)]

    def _decompose_tom_tom(self, fragment: str, base_pos: int) -> List[Dict[str, Any]]:
        sorted_codes = sorted(self.decode_table.keys(), key=len, reverse=True)
        result: List[Dict[str, Any]] = []
        pos = 0
        while pos < len(fragment):
            found_match = False
            for code in sorted_codes:
                if fragment[pos : pos + len(code)] == code:
                    result.append({"value": code, "start": base_pos + pos, "end": base_pos + pos + len(code)})
                    pos += len(code)
                    found_match = True
                    break
            if not found_match:
                pos += 1
        return result

    def _decode_fragment(self, fragment: str) -> str:
        sorted_codes = sorted(self.decode_table.keys(), key=len, reverse=True)
        result: List[str] = []
        pos = 0
        while pos < len(fragment):
            found_match = False
            for code in sorted_codes:
                if fragment[pos : pos + len(code)] == code:
                    result.append(self.decode_table[code])
                    pos += len(code)
                    found_match = True
                    break
            if not found_match:
                pos += 1
        return "".join(result)

    def _extract_tom_tom_fragments(self, text: str, allowed_chars: str) -> List[Dict[str, Any]]:
        fragments: List[Dict[str, Any]] = []
        raw_fragments = self._split_by_separators(text, allowed_chars)
        for fragment in raw_fragments:
            cleaned = fragment.strip(allowed_chars)
            if not cleaned:
                continue
            tom_tom_codes = self._decompose_tom_tom(cleaned, fragment.find(cleaned))
            fragments.extend(tom_tom_codes)
        return fragments

    def _is_all_valid_code(self, text: str, allowed_chars: str) -> bool:
        code_chars = set()
        for code in self.decode_table.keys():
            code_chars.update(code)
        code_chars.update(allowed_chars)
        for char in text:
            if char not in code_chars:
                return False
        fragments = self._split_by_separators(text, allowed_chars)
        for fragment in fragments:
            stripped = fragment.strip(allowed_chars)
            if stripped and not self._decode_fragment(stripped):
                return False
        return True

    def check_code(self, text: str, strict: bool = False, allowed_chars: str | None = None, embedded: bool = False) -> Dict[str, Any]:
        if allowed_chars is None or allowed_chars == "":
            allowed_chars = self.default_separators
        fragments = self._extract_tom_tom_fragments(text, allowed_chars)
        if strict and not embedded:
            if not self._is_all_valid_code(text, allowed_chars):
                return {"is_match": False, "fragments": [], "score": 0.0}
        score = 1.0 if fragments else 0.0
        return {"is_match": bool(fragments), "fragments": fragments, "score": score}

    def decode_fragments(self, text: str, fragments: List[Dict[str, Any]]) -> str:
        sorted_fragments = sorted(fragments, key=lambda f: f["start"], reverse=True)
        result = list(text)
        for fragment in sorted_fragments:
            start, end = fragment["start"], fragment["end"]
            code = fragment["value"]
            if code in self.decode_table:
                decoded = self.decode_table[code]
                result[start:end] = decoded
        return "".join(result)

    def encode(self, text: str) -> str:
        text = text.upper()
        result: List[str] = []
        for char in text:
            if char in self.encode_table:
                result.append(self.encode_table[char])
            elif char in self.default_separators:
                result.append(char)
            else:
                result.append(char)
        return " ".join(result) if result else ""

    def decode(self, text: str) -> str:
        tokens = text.split()
        decoded_tokens: List[str] = []
        for token in tokens:
            if token in self.decode_table:
                decoded_tokens.append(self.decode_table[token])
            else:
                decoded = self._decode_fragment(token)
                decoded_tokens.append(decoded if decoded else token)
        return "".join(decoded_tokens)

    def _clean_text_for_scoring(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text.strip())
        return cleaned

    def _get_score(self, text: str, context: Dict[str, Any]) -> Dict[str, Any] | None:
        if not _SCORING_AVAILABLE or not score_text:
            return None
        try:
            cleaned_text = self._clean_text_for_scoring(text)
            return score_text(cleaned_text, context=context)
        except Exception:
            return None

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        allowed_chars = inputs.get("allowed_chars", self.default_separators)
        embedded = self._is_truthy(inputs.get("embedded", False))
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))
        context = inputs.get("context", {})

        standardized_response = {
            "status": "success",
            "plugin_info": {"name": self.name, "version": self.version, "execution_time": 0},
            "inputs": inputs.copy(),
            "results": [],
            "summary": {"best_result_id": None, "total_results": 0, "message": ""},
        }

        if not isinstance(text, str) or text == "":
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = "Aucun texte fourni à traiter."
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        try:
            if mode == "encode":
                result = self.encode(text)
                standardized_response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": result,
                        "confidence": 1.0,
                        "parameters": {"mode": mode},
                        "metadata": {"processed_chars": len(text)},
                    }
                )
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Encodage Tom Tom réussi",
                    }
                )

            elif mode == "decode":
                result = self.decode(text)
                confidence = 0.5
                scoring_result = None
                if enable_scoring:
                    scoring_result = self._get_score(result, context)
                    if scoring_result and "score" in scoring_result:
                        confidence = float(scoring_result["score"])

                result_item = {
                    "id": "result_1",
                    "text_output": result,
                    "confidence": confidence,
                    "parameters": {"mode": mode, "strict": strict_mode, "embedded": embedded},
                    "metadata": {"processed_chars": len(text)},
                }
                if scoring_result:
                    result_item["scoring"] = scoring_result
                standardized_response["results"].append(result_item)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Décodage Tom Tom réussi",
                    }
                )

            elif mode == "detect":
                detection = self.check_code(text, strict_mode, allowed_chars, embedded)
                if not detection["is_match"]:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = "Aucun code Tom Tom détecté"
                    standardized_response["plugin_info"]["execution_time"] = int(
                        (time.time() - start_time) * 1000
                    )
                    return standardized_response

                decoded_text = self.decode_fragments(text, detection["fragments"])
                confidence = detection.get("score", 0.0)
                scoring_result = None
                if enable_scoring:
                    scoring_result = self._get_score(decoded_text, context)
                    if scoring_result and "score" in scoring_result:
                        confidence = float(scoring_result["score"])

                result_item = {
                    "id": "result_1",
                    "text_output": decoded_text,
                    "confidence": confidence,
                    "parameters": {"mode": mode, "strict": strict_mode, "embedded": embedded},
                    "metadata": {
                        "fragments_found": len(detection.get("fragments", [])),
                        "fragments": detection.get("fragments", []),
                    },
                }
                if scoring_result:
                    result_item["scoring"] = scoring_result

                standardized_response["results"].append(result_item)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Détection Tom Tom effectuée",
                    }
                )

            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode non supporté: {mode}"

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur: {exc}"

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return TomTomPlugin().execute(inputs)
