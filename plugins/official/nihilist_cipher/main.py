from __future__ import annotations

import importlib
import importlib.util
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class NihilistCipherPlugin:
    def __init__(self) -> None:
        self.name = "nihilist_cipher"
        self.version = "1.2.1"

        try:
            from gc_backend.plugins.scoring import score_text

            self._score_text = score_text
            self._scoring_available = True
        except Exception:
            self._score_text = None
            self._scoring_available = False

        self.default_alphabet_mode = "I=J"
        self.default_output_format = "separated"

        self.polybius_plugin = self._load_polybius_plugin()
        if not self.polybius_plugin:
            raise RuntimeError("Le plugin Polybius Square n'est pas disponible")

    def _load_polybius_plugin(self) -> Optional[Any]:
        # Try canonical package import first
        import_paths = [
            "gc_backend.plugins.official.polybius_square.main",
            "plugins.official.polybius_square.main",
        ]

        for module_path in import_paths:
            try:
                module = importlib.import_module(module_path)
                return module.PolybiusSquarePlugin()
            except Exception:
                continue

        # Fallback: load directly from file relative to this plugin
        try:
            base_dir = Path(__file__).resolve().parent
            polybius_path = base_dir / ".." / "polybius_square" / "main.py"
            polybius_path = polybius_path.resolve()
            if polybius_path.exists():
                spec = importlib.util.spec_from_file_location("polybius_square", polybius_path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    return module.PolybiusSquarePlugin()
        except Exception:
            return None

        return None

    def create_grid(self, grid_key: str = "", alphabet_mode: str = "I=J") -> Dict[str, Any]:
        if not self.polybius_plugin:
            raise ValueError("Le plugin Polybius Square n'est pas disponible")

        result = self.polybius_plugin.create_polybius_grid(grid_key, "5x5", alphabet_mode)

        self.grid = result["grid"]
        self.char_to_coords = result["char_to_coords"]
        self.coords_to_char = result["coords_to_char"]
        self.rows = self.cols = 5

        return {
            "grid": self.grid,
            "char_to_coords": self.char_to_coords,
            "coords_to_char": self.coords_to_char,
            "rows": self.rows,
            "cols": self.cols,
        }

    def encode_text_to_coordinates(self, text: str) -> List[Tuple[int, int]]:
        if not self.polybius_plugin:
            raise ValueError("Le plugin Polybius Square n'est pas disponible")

        coordinates: List[Tuple[int, int]] = []
        text = text.upper()
        for char in text:
            if char in self.char_to_coords:
                row, col = self.char_to_coords[char]
                coordinates.append((row, col))
        return coordinates

    def encode_key_to_coordinates(self, key: str) -> List[Tuple[int, int]]:
        return self.encode_text_to_coordinates(key)

    def add_coordinates(
        self, text_coords: List[Tuple[int, int]], key_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        if not text_coords or not key_coords:
            return []

        result: List[Tuple[int, int]] = []
        key_length = len(key_coords)

        for i, text_coord in enumerate(text_coords):
            key_coord = key_coords[i % key_length]
            row_sum = text_coord[0] + key_coord[0]
            col_sum = text_coord[1] + key_coord[1]
            result.append((row_sum, col_sum))

        return result

    def subtract_coordinates(
        self, cipher_coords: List[Tuple[int, int]], key_coords: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        if not cipher_coords or not key_coords:
            return []

        result: List[Tuple[int, int]] = []
        key_length = len(key_coords)

        for i, cipher_coord in enumerate(cipher_coords):
            key_coord = key_coords[i % key_length]
            row_diff = cipher_coord[0] - key_coord[0]
            col_diff = cipher_coord[1] - key_coord[1]
            result.append((row_diff, col_diff))

        return result

    def format_coordinates(self, coords: List[Tuple[int, int]], output_format: str = "separated") -> str:
        if not coords:
            return ""

        number_pairs = [f"{row}{col}" for row, col in coords]
        if output_format == "separated":
            return " ".join(number_pairs)
        return "".join(number_pairs)

    def parse_coordinates(self, text: str) -> List[Tuple[int, int]]:
        if not text:
            return []

        coordinates: List[Tuple[int, int]] = []
        cleaned = re.sub(r"[^0-9\s]", "", text.strip())
        if not cleaned:
            return []

        def parse_token(token: str) -> Optional[Tuple[int, int]]:
            if not token:
                return None
            # Row/col sums range 2..10, so token length can be 2, 3, or 4 (10 + 10)
            if len(token) == 2:
                return int(token[0]), int(token[1])
            if len(token) == 3:
                if token.startswith("10"):
                    return 10, int(token[2])
                return int(token[0]), int(token[1:])
            if len(token) == 4:
                # Only possible case: 10/10
                return 10, 10
            return None

        if " " in cleaned:
            pairs = cleaned.split()
            for pair in pairs:
                parsed = parse_token(pair)
                if parsed:
                    coordinates.append(parsed)
        else:
            # concatenated: walk consuming 2-4 digits per coord
            i = 0
            while i < len(cleaned):
                # prefer 3 or 4 when we see a '1' followed by '0'
                if cleaned[i:i+2] == "10":
                    # try four-digit 1010, else 3-digit 10x
                    if i + 4 <= len(cleaned) and cleaned[i:i+4] == "1010":
                        token = cleaned[i:i+4]
                        i += 4
                    elif i + 3 <= len(cleaned):
                        token = cleaned[i:i+3]
                        i += 3
                    else:
                        break
                else:
                    token = cleaned[i:i+2]
                    i += 2

                parsed = parse_token(token)
                if parsed:
                    coordinates.append(parsed)

        return coordinates

    def encode(
        self,
        text: str,
        key: str,
        output_format: str = "separated",
        grid_key: str = "",
        alphabet_mode: str = "I=J",
    ) -> str:
        if not text or not key:
            return ""

        self.create_grid(grid_key, alphabet_mode)

        text_coords = self.encode_text_to_coordinates(text)
        if not text_coords:
            return ""

        key_coords = self.encode_key_to_coordinates(key)
        if not key_coords:
            return ""

        result_coords = self.add_coordinates(text_coords, key_coords)
        return self.format_coordinates(result_coords, output_format)

    def decode(self, text: str, key: str, grid_key: str = "", alphabet_mode: str = "I=J") -> str:
        if not text or not key:
            return ""

        self.create_grid(grid_key, alphabet_mode)
        cipher_coords = self.parse_coordinates(text)
        if not cipher_coords:
            return ""

        key_coords = self.encode_key_to_coordinates(key)
        if not key_coords:
            return ""

        result_coords = self.subtract_coordinates(cipher_coords, key_coords)

        result: List[str] = []
        for row, col in result_coords:
            coord = (row, col)
            if coord in self.coords_to_char:
                result.append(self.coords_to_char[coord])
            else:
                result.append("?")

        return "".join(result)

    def check_code(self, text: str, strict: bool = False, allowed_chars: Optional[str] = None, embedded: bool = False) -> Dict[str, Any]:
        if not text:
            return {"is_match": False, "score": 0, "fragments": []}

        if allowed_chars is None:
            allowed_chars = " ,.;:!?-_\n\r\t"

        fragments = self._extract_nihilist_fragments(text)
        if not fragments:
            return {"is_match": False, "score": 0, "fragments": []}

        if strict and not embedded:
            if len(fragments) == 1 and fragments[0]["start"] == 0 and fragments[0]["end"] == len(text):
                return {"is_match": True, "score": 0.9, "fragments": fragments}
            return {"is_match": False, "score": 0, "fragments": []}

        total_length = len(text)
        nihilist_length = sum(len(f["value"]) for f in fragments)
        score = 0 if total_length == 0 else min(0.8, nihilist_length / total_length)
        return {"is_match": True, "score": score, "fragments": fragments}

    def _extract_nihilist_fragments(self, text: str) -> List[Dict[str, Any]]:
        fragments: List[Dict[str, Any]] = []

        pattern_separated = r"\b([1-9][0-9](?:\s+[1-9][0-9])+ )\b"
        pattern_concatenated = r"\b([1-9][0-9]{3,}[0-9]*)\b"

        for match in re.finditer(pattern_separated, text):
            fragment = {
                "start": match.start(),
                "end": match.end(),
                "value": match.group(),
                "format": "separated",
                "type": "nihilist_cipher",
            }
            coords = self.parse_coordinates(fragment["value"])
            if coords:
                fragment["coords"] = coords
                fragments.append(fragment)

        for match in re.finditer(pattern_concatenated, text):
            value = match.group()
            if len(value) % 2 == 0:
                fragment = {
                    "start": match.start(),
                    "end": match.end(),
                    "value": value,
                    "format": "concatenated",
                    "type": "nihilist_cipher",
                }
                coords = self.parse_coordinates(fragment["value"])
                if coords:
                    fragment["coords"] = coords
                    fragments.append(fragment)

        return fragments

    def decode_fragments(self, text: str, fragments: List[Dict[str, Any]], key: str) -> str:
        if not fragments or not key:
            return text

        key_coords = self.encode_key_to_coordinates(key)
        if not key_coords:
            return text

        sorted_fragments = sorted(fragments, key=lambda f: f["start"])
        result_chars = list(text)

        for fragment in sorted_fragments:
            cipher_coords = self.parse_coordinates(fragment["value"])
            if not cipher_coords:
                continue

            result_coords = self.subtract_coordinates(cipher_coords, key_coords)
            decoded_chars = []
            for coord in result_coords:
                if coord in self.coords_to_char:
                    decoded_chars.append(self.coords_to_char[coord])
                else:
                    decoded_chars.append("?")

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
        grid_key = inputs.get("grid_key", "")
        alphabet_mode = inputs.get("alphabet_mode", self.default_alphabet_mode)
        output_format = inputs.get("output_format", self.default_output_format).lower()
        strict_mode = str(inputs.get("strict", "")).lower() == "strict"
        allowed_chars = inputs.get("allowed_chars", " ,.;:!?-_\n\r\t")
        embedded = bool(inputs.get("embedded", False))
        enable_scoring = bool(inputs.get("enable_scoring", True))
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

        if mode != "detect" and not key:
            result["status"] = "error"
            result["summary"]["message"] = "Clé de surchiffrement requise."
            result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return result

        if mode != "detect":
            try:
                self.create_grid(grid_key, alphabet_mode)
            except Exception as exc:
                result["status"] = "error"
                result["summary"]["message"] = f"Erreur lors de la création de la grille: {exc}"
                result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                return result

        try:
            if mode == "encode":
                encoded = self.encode(text, key, output_format, grid_key, alphabet_mode)
                result_item = {
                    "id": "result_1",
                    "text_output": encoded,
                    "confidence": 1.0,
                    "parameters": {
                        "mode": mode,
                        "key": key,
                        "output_format": output_format,
                        "alphabet_mode": alphabet_mode,
                    },
                    "metadata": {
                        "processed_chars": len(text),
                        "key_length": len(key),
                    },
                }

                result["results"].append(result_item)
                result["summary"]["total_results"] = 1
                result["summary"]["best_result_id"] = "result_1"
                result["summary"]["message"] = "Texte encodé avec succès"

            elif mode == "decode":
                check_result = self.check_code(text, strict_mode, allowed_chars, embedded)

                if check_result["is_match"]:
                    if strict_mode and not embedded:
                        decoded = self.decode(text, key, grid_key, alphabet_mode)
                        confidence = 0.9
                    else:
                        decoded = self.decode_fragments(text, check_result["fragments"], key)
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
                            "key": key,
                            "strict": strict_mode,
                            "alphabet_mode": alphabet_mode,
                        },
                        "metadata": {
                            "processed_chars": len(text),
                            "fragments_found": len(check_result["fragments"]),
                            "key_length": len(key),
                        },
                    }

                    if scoring_result:
                        result_item["scoring"] = scoring_result

                    result["results"].append(result_item)
                    result["summary"]["best_result_id"] = "result_1"
                    result["summary"]["total_results"] = 1
                    result["summary"]["message"] = "Décodage réussi"
                else:
                    result["status"] = "error"
                    result["summary"]["message"] = "Aucun code Nihiliste valide trouvé dans le texte"
            elif mode == "detect":
                check_result = self.check_code(text, strict_mode, allowed_chars, embedded)
                confidence = float(check_result.get("score", 0.0)) if check_result else 0.0

                result_item = {
                    "id": "result_1",
                    "text_output": "Code Nihiliste détecté" if check_result["is_match"] else "Aucun code Nihiliste détecté",
                    "confidence": confidence,
                    "parameters": {
                        "mode": mode,
                        "strict": strict_mode,
                        "embedded": embedded,
                    },
                    "metadata": {
                        "is_match": bool(check_result["is_match"]),
                        "fragments_found": len(check_result.get("fragments", [])),
                    },
                }

                result["results"].append(result_item)
                result["summary"]["best_result_id"] = "result_1"
                result["summary"]["total_results"] = 1
                result["summary"]["message"] = "Détection terminée"
            else:
                result["status"] = "error"
                result["summary"]["message"] = f"Mode non supporté: {mode}"

        except Exception as exc:
            result["status"] = "error"
            result["summary"]["message"] = f"Erreur lors du traitement: {exc}"

        result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return result


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return NihilistCipherPlugin().execute(inputs)
