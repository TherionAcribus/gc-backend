from __future__ import annotations

import re
import string
import time
from typing import Any, Dict, List, Optional, Tuple


class PolybiusSquarePlugin:
    def __init__(self) -> None:
        self.name = "polybius_square"
        self.version = "1.0.1"

        try:
            from gc_backend.plugins.scoring import score_text

            self._score_text = score_text
            self._scoring_available = True
        except Exception:
            self._score_text = None
            self._scoring_available = False

    def create_polybius_grid(self, key: str = "", grid_size: str = "5x5", alphabet_mode: str = "I=J") -> Dict[str, Any]:
        grid_dim = 6 if grid_size == "6x6" else 5

        if grid_size == "6x6":
            alphabet = list(string.ascii_uppercase) + list(string.digits)
        else:
            alphabet = list(string.ascii_uppercase)
            if alphabet_mode == "I=J" and "J" in alphabet:
                alphabet.remove("J")
            elif alphabet_mode == "C=K" and "K" in alphabet:
                alphabet.remove("K")
            elif alphabet_mode == "W=VV" and "W" in alphabet:
                alphabet.remove("W")

        if key:
            key = key.upper()
            unique_key_chars: List[str] = []
            for char in key:
                if char in alphabet and char not in unique_key_chars:
                    unique_key_chars.append(char)
            for char in unique_key_chars:
                if char in alphabet:
                    alphabet.remove(char)
            alphabet = unique_key_chars + alphabet

        grid: List[List[str]] = []
        char_to_coords: Dict[str, Tuple[int, int]] = {}
        coords_to_char: Dict[Tuple[int, int], str] = {}

        for i in range(grid_dim):
            row: List[str] = []
            for j in range(grid_dim):
                idx = i * grid_dim + j
                if idx < len(alphabet):
                    char = alphabet[idx]
                    row.append(char)
                    char_to_coords[char] = (i + 1, j + 1)
                    coords_to_char[(i + 1, j + 1)] = char
                else:
                    row.append(" ")
                    coords_to_char[(i + 1, j + 1)] = " "
            grid.append(row)

        if grid_size == "5x5":
            if alphabet_mode == "I=J":
                for coord, char in coords_to_char.items():
                    if char == "I":
                        char_to_coords["J"] = coord
            elif alphabet_mode == "C=K":
                for coord, char in coords_to_char.items():
                    if char == "C":
                        char_to_coords["K"] = coord

        reverse_grid = {coord: char for coord, char in coords_to_char.items()}

        return {
            "grid": grid,
            "grid_dim": grid_dim,
            "char_to_coords": char_to_coords,
            "coords_to_char": coords_to_char,
            "reverse_grid": reverse_grid,
        }

    def format_coordinates(self, row: int, col: int, output_format: str = "numbers") -> str:
        if output_format == "coordinates":
            return f"({row},{col})"
        return f"{row}{col}"

    def decode_coordinates(self, text: str, grid_dim: int = 5) -> List[Tuple[int, int]]:
        coords: List[Tuple[int, int]] = []

        number_matches = re.findall(r"(\d{2})", text)
        for match in number_matches:
            if len(match) == 2:
                row = int(match[0])
                col = int(match[1])
                if 1 <= row <= grid_dim and 1 <= col <= grid_dim:
                    coords.append((row, col))

        if coords:
            return coords

        coord_matches = re.findall(r"\(\s*(\d)\s*,\s*(\d)\s*\)", text)
        for match in coord_matches:
            row = int(match[0])
            col = int(match[1])
            if 1 <= row <= grid_dim and 1 <= col <= grid_dim:
                coords.append((row, col))

        return coords

    def encode(self, text: str, key: str = "", output_format: str = "numbers", grid_size: str = "5x5", alphabet_mode: str = "I=J") -> str:
        grid_data = self.create_polybius_grid(key, grid_size, alphabet_mode)
        text = text.upper()

        result: List[str] = []
        for char in text:
            if char == " ":
                result.append(" ")
                continue

            if alphabet_mode == "W=VV" and grid_size == "5x5" and char == "W":
                if "V" in grid_data["char_to_coords"]:
                    v_coords = grid_data["char_to_coords"]["V"]
                    result.append(self.format_coordinates(v_coords[0], v_coords[1], output_format))
                    result.append(self.format_coordinates(v_coords[0], v_coords[1], output_format))
                continue

            if char in grid_data["char_to_coords"]:
                coords = grid_data["char_to_coords"][char]
                result.append(self.format_coordinates(coords[0], coords[1], output_format))

        return "".join(result)

    def decode(self, text: str, key: str = "", grid_size: str = "5x5", alphabet_mode: str = "I=J") -> str:
        grid_data = self.create_polybius_grid(key, grid_size, alphabet_mode)
        reverse_grid = grid_data["reverse_grid"]
        grid_dim = 6 if grid_size == "6x6" else 5

        result: List[str] = []
        parts = text.split(" ")

        for idx, part in enumerate(parts):
            if not part:
                if idx > 0:
                    result.append(" ")
                continue

            coordinates = self.decode_coordinates(part, grid_dim)
            if not coordinates:
                continue

            decoded_chars: List[str] = []
            i = 0
            while i < len(coordinates):
                coord = coordinates[i]

                if alphabet_mode == "W=VV" and grid_size == "5x5" and i < len(coordinates) - 1:
                    next_coord = coordinates[i + 1]
                    if coord in reverse_grid and next_coord in reverse_grid:
                        if reverse_grid[coord] == "V" and reverse_grid[next_coord] == "V":
                            decoded_chars.append("W")
                            i += 2
                            continue

                if coord in reverse_grid:
                    decoded_chars.append(reverse_grid[coord])
                else:
                    decoded_chars.append("?")
                i += 1

            result.append("".join(decoded_chars))
            if idx < len(parts) - 1:
                result.append(" ")

        return "".join(result)

    def check_code(
        self,
        text: str,
        strict: bool = False,
        allowed_chars: Optional[str] = None,
        embedded: bool = False,
        grid_size: str = "5x5",
        alphabet_mode: str = "I=J",
    ) -> Dict[str, Any]:
        if not text:
            return {"is_match": False, "score": 0, "fragments": []}

        if allowed_chars is None:
            allowed_chars = " ,.;:!?-_"

        grid_dim = 6 if grid_size == "6x6" else 5
        fragments = self._extract_polybius_fragments(text, grid_dim)
        if not fragments:
            return {"is_match": False, "score": 0, "fragments": []}

        if strict and not embedded:
            if len(fragments) == 1 and fragments[0]["start"] == 0 and fragments[0]["end"] == len(text):
                return {"is_match": True, "score": 0.9, "fragments": fragments}
            return {"is_match": False, "score": 0, "fragments": []}

        total_length = len(text)
        polybius_length = sum(len(f["value"]) for f in fragments)
        score = 0 if total_length == 0 else min(0.8, polybius_length / total_length)
        return {"is_match": True, "score": score, "fragments": fragments}

    def _extract_polybius_fragments(self, text: str, grid_dim: int = 5) -> List[Dict[str, Any]]:
        fragments: List[Dict[str, Any]] = []

        if grid_dim == 5:
            number_pattern = r"[1-5]{2}"
            coord_pattern = r"\([1-5],[1-5]\)"
        else:
            number_pattern = r"[1-6]{2}"
            coord_pattern = r"\([1-6],[1-6]\)"

        combined_pattern = f"({number_pattern}|{coord_pattern})"
        matches = list(re.finditer(combined_pattern, text))
        if not matches:
            return []

        current_fragment = {
            "start": matches[0].start(),
            "end": matches[0].end(),
            "value": matches[0].group(),
            "coords": [],
            "type": "numbers" if re.match(number_pattern, matches[0].group()) else "coordinates",
        }

        coords = self.decode_coordinates(matches[0].group(), grid_dim)
        if coords:
            current_fragment["coords"] = coords

        for i in range(1, len(matches)):
            match = matches[i]
            prev_match = matches[i - 1]

            coords = self.decode_coordinates(match.group(), grid_dim)
            if not coords:
                continue

            if match.start() - prev_match.end() <= 2:
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
                    "type": "numbers" if re.match(number_pattern, match.group()) else "coordinates",
                }

        if current_fragment["coords"]:
            fragments.append(current_fragment)

        return fragments

    def decode_fragments(
        self,
        text: str,
        fragments: List[Dict[str, Any]],
        key: str = "",
        grid_size: str = "5x5",
        alphabet_mode: str = "I=J",
    ) -> str:
        if not fragments:
            return text

        grid_data = self.create_polybius_grid(key, grid_size, alphabet_mode)
        sorted_fragments = sorted(fragments, key=lambda f: f["start"])
        result_chars = list(text)
        last_end = 0

        for fragment in sorted_fragments:
            decoded_chars: List[str] = []
            i = 0
            coords = fragment["coords"]
            while i < len(coords):
                coord = coords[i]

                if alphabet_mode == "W=VV" and grid_size == "5x5" and i < len(coords) - 1:
                    next_coord = coords[i + 1]
                    if coord in grid_data["reverse_grid"] and next_coord in grid_data["reverse_grid"]:
                        if grid_data["reverse_grid"][coord] == "V" and grid_data["reverse_grid"][next_coord] == "V":
                            decoded_chars.append("W")
                            i += 2
                            continue

                if coord in grid_data["reverse_grid"]:
                    decoded_chars.append(grid_data["reverse_grid"][coord])
                else:
                    decoded_chars.append("?")
                i += 1

            decoded_text = "".join(decoded_chars)
            fragment_length = fragment["end"] - fragment["start"]
            decoded_length = len(decoded_text)

            if decoded_length < fragment_length:
                decoded_text = decoded_text + " " * (fragment_length - decoded_length)
            elif decoded_length > fragment_length:
                decoded_text = decoded_text[:fragment_length]

            for j in range(fragment_length):
                if j < len(decoded_text):
                    result_chars[fragment["start"] + j] = decoded_text[j]

            last_end = fragment["end"]

        if last_end < len(text):
            result_chars[last_end:] = list(text[last_end:])

        return "".join(result_chars)

    def _clean_text_for_scoring(self, text: str) -> str:
        cleaned = re.sub(r"[^\w\s]", "", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _get_text_score(self, text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not self._scoring_available or not self._score_text:
            return None
        cleaned_text = self._clean_text_for_scoring(text)
        try:
            return self._score_text(cleaned_text, context=context or {})
        except Exception:
            return None

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        key = inputs.get("key", "")
        grid_size = inputs.get("grid_size", "5x5")
        alphabet_mode = inputs.get("alphabet_mode", "I=J")
        output_format = inputs.get("output_format", "numbers").lower()
        strict_mode = str(inputs.get("strict", "")).lower() == "strict"
        allowed_chars = inputs.get("allowed_chars", " ,.;:!?-_")
        embedded = bool(inputs.get("embedded", False))
        enable_scoring = bool(inputs.get("enable_scoring", False))
        context = inputs.get("context", {})

        result = {
            "status": "success",
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time": 0,
            },
            "inputs": inputs,
            "results": [],
            "summary": {
                "total_results": 0,
                "best_result_id": None,
            },
        }

        if not text:
            result["status"] = "error"
            result["summary"]["message"] = "Aucun texte fourni à traiter."
            result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return result

        try:
            if mode == "encode":
                encoded = self.encode(text, key, output_format, grid_size, alphabet_mode)
                result_item = {
                    "id": "result_1",
                    "text_output": encoded,
                    "confidence": 1.0,
                    "parameters": {
                        "mode": mode,
                        "key": key if key else "(aucune)",
                        "grid_size": grid_size,
                        "alphabet_mode": alphabet_mode,
                        "output_format": output_format,
                    },
                    "metadata": {
                        "processed_chars": len(text),
                    },
                }

                result["results"].append(result_item)
                result["summary"]["total_results"] = 1
                result["summary"]["best_result_id"] = "result_1"
                result["summary"]["message"] = "Texte encodé avec succès"

            elif mode == "decode":
                check_result = self.check_code(text, strict_mode, allowed_chars, embedded, grid_size, alphabet_mode)

                if check_result["is_match"]:
                    if strict_mode and not embedded:
                        decoded = self.decode(text, key, grid_size, alphabet_mode)
                        confidence = 0.9
                    else:
                        decoded = self.decode_fragments(text, check_result["fragments"], key, grid_size, alphabet_mode)
                        confidence = check_result["score"]

                    scoring_result = None
                    if enable_scoring:
                        scoring_result = self._get_text_score(decoded, context)
                        if scoring_result:
                            confidence = float(scoring_result.get("score", confidence))

                    result_item = {
                        "id": "result_1",
                        "text_output": decoded,
                        "confidence": confidence,
                        "parameters": {
                            "mode": mode,
                            "key": key if key else "(aucune)",
                            "grid_size": grid_size,
                            "alphabet_mode": alphabet_mode,
                            "strict": "strict" if strict_mode else "smooth",
                        },
                        "metadata": {
                            "processed_chars": len(text),
                            "decoded_chars": len("".join(f["value"] for f in check_result["fragments"])),
                        },
                    }

                    if scoring_result:
                        result_item["scoring"] = scoring_result

                    result["results"].append(result_item)
                    result["summary"]["total_results"] = 1
                    result["summary"]["best_result_id"] = "result_1"
                    result["summary"]["message"] = "Texte décodé avec succès"
                else:
                    result["status"] = "error"
                    result["summary"]["message"] = "Aucun code Polybe valide n'a été trouvé dans le texte fourni."
            else:
                result["status"] = "error"
                result["summary"]["message"] = f"Mode non reconnu: {mode}. Utilisez 'encode' ou 'decode'."

        except Exception as exc:
            result["status"] = "error"
            result["summary"]["message"] = f"Erreur lors du traitement: {exc}"

        result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return result


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return PolybiusSquarePlugin().execute(inputs)
