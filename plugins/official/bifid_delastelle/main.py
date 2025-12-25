"""Plugin Bifid de Delastelle pour MysterAI.

Le chiffre bifide a été inventé par Félix-Marie Delastelle en 1895.
"""

from __future__ import annotations

import string
import time
from typing import Any, Dict, List, Tuple


class BifidDelastellePlugin:
    """Chiffre bifide de Delastelle basé sur une grille de Polybe (5x5 ou 6x6)."""

    def __init__(self) -> None:
        self.name = "bifid_delastelle"
        self.version = "1.0.0"

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"

        key = str(inputs.get("key", "") or "")
        grid_size = str(inputs.get("grid_size", "5x5"))
        alphabet_mode = str(inputs.get("alphabet_mode", "I=J"))
        coordinate_order = str(inputs.get("coordinate_order", "ligne-colonne"))

        try:
            period = int(inputs.get("period", 5))
        except (TypeError, ValueError):
            return self._error_response("La période doit être un nombre", start_time)

        if not isinstance(text, str) or not text.strip():
            return self._error_response("Aucun texte fourni", start_time)

        if period < 1 or period > 50:
            return self._error_response("La période doit être comprise entre 1 et 50", start_time)

        grid_info = self._create_polybius_grid(key=key, grid_size=grid_size, alphabet_mode=alphabet_mode)
        valid_chars = set(grid_info["char_to_coords"].keys())

        if strict_mode and not self._is_text_strictly_compatible(text, valid_chars=valid_chars):
            return self._error_response("Texte incompatible avec Bifid (mode strict)", start_time)

        if mode == "encode":
            output = self.encode(
                text=text,
                key=key,
                grid_size=grid_size,
                alphabet_mode=alphabet_mode,
                period=period,
                coordinate_order=coordinate_order,
            )
            return {
                "status": "ok",
                "summary": "Encodage bifide réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 1.0,
                        "parameters": {
                            "mode": mode,
                            "key": key,
                            "grid_size": grid_size,
                            "alphabet_mode": alphabet_mode,
                            "period": period,
                            "coordinate_order": coordinate_order,
                        },
                        "metadata": {"output_chars": len(output)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "decode":
            output = self.decode(
                text=text,
                key=key,
                grid_size=grid_size,
                alphabet_mode=alphabet_mode,
                period=period,
                coordinate_order=coordinate_order,
            )
            return {
                "status": "ok",
                "summary": "Décodage bifide réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 0.5,
                        "parameters": {
                            "mode": mode,
                            "key": key,
                            "grid_size": grid_size,
                            "alphabet_mode": alphabet_mode,
                            "period": period,
                            "coordinate_order": coordinate_order,
                        },
                        "metadata": {"output_chars": len(output)},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        return self._error_response(f"Mode inconnu: {mode}", start_time)

    def encode(
        self,
        text: str,
        key: str = "",
        grid_size: str = "5x5",
        alphabet_mode: str = "I=J",
        period: int = 5,
        coordinate_order: str = "ligne-colonne",
    ) -> str:
        """Encode un texte avec le chiffre bifide de Delastelle."""
        if not text:
            return ""

        grid_info = self._create_polybius_grid(key=key, grid_size=grid_size, alphabet_mode=alphabet_mode)
        char_to_coords = grid_info["char_to_coords"]
        coords_to_char = grid_info["coords_to_char"]

        clean_text = text.upper().replace(" ", "")
        if grid_size == "5x5" and alphabet_mode == "W=VV":
            clean_text = clean_text.replace("W", "VV")

        coordinates: List[Tuple[int, int]] = []
        for ch in clean_text:
            if ch not in char_to_coords:
                continue
            row, col = char_to_coords[ch]
            if coordinate_order == "colonne-ligne":
                coordinates.append((col, row))
            else:
                coordinates.append((row, col))

        result_coords: List[Tuple[int, int]] = []
        for i in range(0, len(coordinates), period):
            block = coordinates[i : i + period]
            if not block:
                continue

            first_coords = [coord[0] for coord in block]
            second_coords = [coord[1] for coord in block]
            combined = first_coords + second_coords

            for j in range(0, len(combined) - 1, 2):
                result_coords.append((combined[j], combined[j + 1]))

        result = ""
        for coord in result_coords:
            if coord in coords_to_char:
                result += coords_to_char[coord]

        return result

    def decode(
        self,
        text: str,
        key: str = "",
        grid_size: str = "5x5",
        alphabet_mode: str = "I=J",
        period: int = 5,
        coordinate_order: str = "ligne-colonne",
    ) -> str:
        """Décode un texte chiffré avec le chiffre bifide de Delastelle."""
        if not text:
            return ""

        grid_info = self._create_polybius_grid(key=key, grid_size=grid_size, alphabet_mode=alphabet_mode)
        char_to_coords = grid_info["char_to_coords"]
        coords_to_char = grid_info["coords_to_char"]

        clean_text = text.upper().replace(" ", "")

        coordinates: List[Tuple[int, int]] = []
        for ch in clean_text:
            if ch not in char_to_coords:
                continue
            row, col = char_to_coords[ch]
            coordinates.append((row, col))

        result_coords: List[Tuple[int, int]] = []

        for i in range(0, len(coordinates), period):
            block = coordinates[i : i + period]
            if not block:
                continue

            coord_values: List[int] = []
            for coord in block:
                coord_values.extend([coord[0], coord[1]])

            mid = len(coord_values) // 2
            first_half = coord_values[:mid]
            second_half = coord_values[mid:]

            for j in range(min(len(first_half), len(second_half))):
                if coordinate_order == "colonne-ligne":
                    original_coord = (second_half[j], first_half[j])
                else:
                    original_coord = (first_half[j], second_half[j])
                result_coords.append(original_coord)

        result = ""
        for coord in result_coords:
            if coord in coords_to_char:
                result += coords_to_char[coord]

        return result

    def _create_polybius_grid(self, key: str, grid_size: str, alphabet_mode: str) -> Dict[str, Any]:
        grid_dim = 6 if grid_size == "6x6" else 5

        if grid_size == "6x6":
            alphabet: List[str] = list(string.ascii_uppercase) + list(string.digits)
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

        char_to_coords: Dict[str, Tuple[int, int]] = {}
        coords_to_char: Dict[Tuple[int, int], str] = {}

        for i in range(grid_dim):
            for j in range(grid_dim):
                idx = i * grid_dim + j
                if idx >= len(alphabet):
                    continue
                char = alphabet[idx]
                char_to_coords[char] = (i + 1, j + 1)
                coords_to_char[(i + 1, j + 1)] = char

        if grid_size == "5x5":
            if alphabet_mode == "I=J" and "I" in char_to_coords:
                char_to_coords["J"] = char_to_coords["I"]
            elif alphabet_mode == "C=K" and "C" in char_to_coords:
                char_to_coords["K"] = char_to_coords["C"]

        return {"char_to_coords": char_to_coords, "coords_to_char": coords_to_char, "grid_dim": grid_dim}

    def _is_text_strictly_compatible(self, text: str, valid_chars: set[str]) -> bool:
        has_valid = False
        for ch in text.upper():
            if ch in valid_chars:
                has_valid = True
                continue
            if ch.isspace():
                continue
            return False
        return has_valid

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
    """Point d'entrée pour le système de plugins."""
    return BifidDelastellePlugin().execute(inputs)
