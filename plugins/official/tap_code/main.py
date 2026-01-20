from __future__ import annotations

import re
import string
import time
from typing import Any, Dict, List, Tuple

try:
    from gc_backend.plugins.scoring import score_text

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text = None
    _SCORING_AVAILABLE = False


class TapCodePlugin:
    def __init__(self) -> None:
        self.name = "tap_code"
        self.version = "1.0.0"
        self._create_tap_code_grid()

    def _create_tap_code_grid(self) -> None:
        alphabet = list(string.ascii_uppercase)
        if "K" in alphabet:
            alphabet.remove("K")

        self.grid: List[List[str]] = []
        self.char_to_coords: Dict[str, Tuple[int, int]] = {}
        self.coords_to_char: Dict[Tuple[int, int], str] = {}

        for i in range(5):
            row: List[str] = []
            for j in range(5):
                idx = i * 5 + j
                char = alphabet[idx] if idx < len(alphabet) else " "
                row.append(char)
                self.char_to_coords[char] = (i + 1, j + 1)
                self.coords_to_char[(i + 1, j + 1)] = char
            self.grid.append(row)

        # K is merged with C
        self.char_to_coords["K"] = self.char_to_coords["C"]

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    def _format_tap_coordinates(self, row: int, col: int, output_format: str) -> str:
        if output_format == "dots":
            return f"{'.' * row} {'.' * col}"
        if output_format == "numbers":
            return f"{row} {col}"
        return f"{'X' * row} {'X' * col}"

    def _decode_tap_coordinates(self, text: str) -> List[Tuple[int, int]]:
        coords: List[Tuple[int, int]] = []
        patterns = [
            (r"(X+)\s+(X+)", lambda m: (len(m.group(1)), len(m.group(2)))),
            (r"(\.+)\s+(\.+)", lambda m: (len(m.group(1)), len(m.group(2)))),
            (r"([1-5])\s+([1-5])", lambda m: (int(m.group(1)), int(m.group(2)))),
        ]

        for pattern, extract_func in patterns:
            for match in re.finditer(pattern, text):
                row, col = extract_func(match)
                if 1 <= row <= 5 and 1 <= col <= 5:
                    coords.append((row, col))
        return coords

    def encode(self, text: str, output_format: str = "taps") -> str:
        text = text.upper()
        result: List[str] = []
        for char in text:
            if char == " ":
                result.append(" ")
                continue
            if char in self.char_to_coords:
                row, col = self.char_to_coords[char]
                result.append(self._format_tap_coordinates(row, col, output_format))
        return " ".join(result)

    def decode(self, text: str) -> str:
        coordinates = self._decode_tap_coordinates(text)
        if not coordinates:
            return ""
        chars = [self.coords_to_char.get(coord, "?") for coord in coordinates]
        return "".join(chars)

    def _extract_tap_fragments(self, text: str) -> List[Dict[str, Any]]:
        patterns = [
            r"(X+)\s+(X+)",
            r"(\.+)\s+(\.+)",
            r"([1-5])\s+([1-5])",
        ]
        combined_pattern = "|".join(f"({pattern})" for pattern in patterns)
        matches = list(re.finditer(combined_pattern, text))
        if not matches:
            return []

        fragments: List[Dict[str, Any]] = []
        current_fragment = {
            "start": matches[0].start(),
            "end": matches[0].end(),
            "value": matches[0].group(),
            "coords": [],
            "type": "tap_code",
        }
        coords = self._decode_tap_coordinates(matches[0].group())
        if coords:
            current_fragment["coords"] = coords

        for i in range(1, len(matches)):
            match = matches[i]
            prev_match = matches[i - 1]
            coords = self._decode_tap_coordinates(match.group())
            if not coords:
                continue
            if match.start() - prev_match.end() <= 3:
                current_fragment["end"] = match.end()
                current_fragment["value"] += text[prev_match.end() : match.end()]
                current_fragment["coords"].extend(coords)
            else:
                if current_fragment["coords"]:
                    fragments.append(current_fragment)
                current_fragment = {
                    "start": match.start(),
                    "end": match.end(),
                    "value": match.group(),
                    "coords": coords,
                    "type": "tap_code",
                }

        if current_fragment["coords"]:
            fragments.append(current_fragment)
        return fragments

    def check_code(self, text: str, strict: bool = False, allowed_chars: str | None = None, embedded: bool = False) -> Dict[str, Any]:
        if not text:
            return {"is_match": False, "score": 0.0, "fragments": []}
        if allowed_chars is None or allowed_chars == "":
            allowed_chars = " ,.;:!?-_"

        fragments = self._extract_tap_fragments(text)
        if not fragments:
            return {"is_match": False, "score": 0.0, "fragments": []}

        if strict and not embedded:
            if len(fragments) == 1 and fragments[0]["start"] == 0 and fragments[0]["end"] == len(text):
                return {"is_match": True, "score": 0.9, "fragments": fragments}
            return {"is_match": False, "score": 0.0, "fragments": []}

        total_length = len(text)
        tap_length = sum(len(f["value"]) for f in fragments)
        score = min(0.8, tap_length / total_length) if total_length else 0.0
        return {"is_match": True, "score": score, "fragments": fragments}

    def decode_fragments(self, text: str, fragments: List[Dict[str, Any]]) -> str:
        if not fragments:
            return text

        result_chars = list(text)
        for fragment in sorted(fragments, key=lambda f: f["start"]):
            decoded_chars = [self.coords_to_char.get(coord, "?") for coord in fragment["coords"]]
            decoded_text = "".join(decoded_chars)
            fragment_length = fragment["end"] - fragment["start"]

            if len(decoded_text) < fragment_length:
                decoded_text += " " * (fragment_length - len(decoded_text))
            elif len(decoded_text) > fragment_length:
                decoded_text = decoded_text[:fragment_length]

            for offset, ch in enumerate(decoded_text[:fragment_length]):
                result_chars[fragment["start"] + offset] = ch

        return "".join(result_chars)

    def _clean_text_for_scoring(self, text: str) -> str:
        cleaned = re.sub(r"[^\w\s]", "", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
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
        output_format = str(inputs.get("output_format", "taps")).lower()
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        allowed_chars = inputs.get("allowed_chars")
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
                encoded = self.encode(text, output_format)
                result_item = {
                    "id": "result_1",
                    "text_output": encoded,
                    "confidence": 1.0,
                    "parameters": {"mode": mode, "output_format": output_format},
                    "metadata": {"processed_chars": len(text)},
                }
                standardized_response["results"].append(result_item)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Texte encodé avec succès",
                    }
                )

            elif mode == "decode":
                check_result = self.check_code(text, strict_mode, allowed_chars, embedded)
                if not check_result["is_match"]:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = "Aucun code Tap valide trouvé dans le texte"
                    standardized_response["plugin_info"]["execution_time"] = int(
                        (time.time() - start_time) * 1000
                    )
                    return standardized_response

                if strict_mode and not embedded:
                    decoded = self.decode(text)
                    confidence = 0.9
                else:
                    decoded = self.decode_fragments(text, check_result["fragments"])
                    confidence = check_result["score"]

                scoring_result = self._get_score(decoded, context) if enable_scoring else None
                if scoring_result and "score" in scoring_result:
                    confidence = float(scoring_result["score"])

                result_item = {
                    "id": "result_1",
                    "text_output": decoded,
                    "confidence": confidence,
                    "parameters": {"mode": mode, "strict": "strict" if strict_mode else "smooth"},
                    "metadata": {
                        "processed_chars": len(text),
                        "fragments_found": len(check_result["fragments"]),
                    },
                }
                if scoring_result:
                    result_item["scoring"] = scoring_result

                standardized_response["results"].append(result_item)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Décodage réussi",
                    }
                )

            elif mode == "detect":
                check_result = self.check_code(text, strict=False, allowed_chars=allowed_chars, embedded=True)
                if not check_result["is_match"]:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = "Aucun code Tap détecté"
                    standardized_response["plugin_info"]["execution_time"] = int(
                        (time.time() - start_time) * 1000
                    )
                    return standardized_response

                decoded = self.decode_fragments(text, check_result["fragments"])
                confidence = check_result["score"]
                scoring_result = self._get_score(decoded, context) if enable_scoring else None
                if scoring_result and "score" in scoring_result:
                    confidence = float(scoring_result["score"])

                result_item = {
                    "id": "result_1",
                    "text_output": decoded,
                    "confidence": confidence,
                    "parameters": {"mode": "detect"},
                    "metadata": {
                        "fragments_count": len(check_result["fragments"]),
                        "fragments": check_result["fragments"],
                    },
                }
                if scoring_result:
                    result_item["scoring"] = scoring_result

                standardized_response["results"].append(result_item)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": f"{len(check_result['fragments'])} fragment(s) détecté(s)",
                    }
                )

            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode non supporté: {mode}"

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur lors du traitement: {exc}"

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return TapCodePlugin().execute(inputs)
