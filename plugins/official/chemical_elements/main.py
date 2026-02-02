"""Plugin Chemical Elements pour MysterAI.

Convertit :
- symboles chimiques isolés -> numéros atomiques (decode)
- numéros atomiques isolés -> symboles chimiques (encode)
- détecte la présence de symboles chimiques isolés (detect)

Le traitement se fait sur des "mots" séparés par des séparateurs (espaces / ponctuation).
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional


DEFAULT_ALLOWED_CHARS = " \t\r\n.:;,_-°"
NON_BREAKING_WHITESPACES = "\u00a0\u202f"


class ChemicalElementsPlugin:
    def __init__(self) -> None:
        self.name = "chemical_elements"
        self.version = "1.1.0"

        self.element_to_number: Dict[str, int] = {
            "H": 1,
            "He": 2,
            "Li": 3,
            "Be": 4,
            "B": 5,
            "C": 6,
            "N": 7,
            "O": 8,
            "F": 9,
            "Ne": 10,
            "Na": 11,
            "Mg": 12,
            "Al": 13,
            "Si": 14,
            "P": 15,
            "S": 16,
            "Cl": 17,
            "Ar": 18,
            "K": 19,
            "Ca": 20,
            "Sc": 21,
            "Ti": 22,
            "V": 23,
            "Cr": 24,
            "Mn": 25,
            "Fe": 26,
            "Co": 27,
            "Ni": 28,
            "Cu": 29,
            "Zn": 30,
            "Ga": 31,
            "Ge": 32,
            "As": 33,
            "Se": 34,
            "Br": 35,
            "Kr": 36,
            "Rb": 37,
            "Sr": 38,
            "Y": 39,
            "Zr": 40,
            "Nb": 41,
            "Mo": 42,
            "Tc": 43,
            "Ru": 44,
            "Rh": 45,
            "Pd": 46,
            "Ag": 47,
            "Cd": 48,
            "In": 49,
            "Sn": 50,
            "Sb": 51,
            "Te": 52,
            "I": 53,
            "Xe": 54,
            "Cs": 55,
            "Ba": 56,
            "La": 57,
            "Ce": 58,
            "Pr": 59,
            "Nd": 60,
            "Pm": 61,
            "Sm": 62,
            "Eu": 63,
            "Gd": 64,
            "Tb": 65,
            "Dy": 66,
            "Ho": 67,
            "Er": 68,
            "Tm": 69,
            "Yb": 70,
            "Lu": 71,
            "Hf": 72,
            "Ta": 73,
            "W": 74,
            "Re": 75,
            "Os": 76,
            "Ir": 77,
            "Pt": 78,
            "Au": 79,
            "Hg": 80,
            "Tl": 81,
            "Pb": 82,
            "Bi": 83,
            "Po": 84,
            "At": 85,
            "Rn": 86,
            "Fr": 87,
            "Ra": 88,
            "Ac": 89,
            "Th": 90,
            "Pa": 91,
            "U": 92,
            "Np": 93,
            "Pu": 94,
            "Am": 95,
            "Cm": 96,
            "Bk": 97,
            "Cf": 98,
            "Es": 99,
            "Fm": 100,
            "Md": 101,
            "No": 102,
            "Lr": 103,
            "Rf": 104,
            "Db": 105,
            "Sg": 106,
            "Bh": 107,
            "Hs": 108,
            "Mt": 109,
            "Ds": 110,
            "Rg": 111,
            "Cn": 112,
            "Nh": 113,
            "Fl": 114,
            "Mc": 115,
            "Lv": 116,
            "Ts": 117,
            "Og": 118,
        }

        self.number_to_element: Dict[int, str] = {v: k for k, v in self.element_to_number.items()}

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"

        allowed_chars = self._prepare_allowed_chars(inputs.get("allowed_chars"))

        embedded = bool(inputs.get("embedded", True))

        if not isinstance(text, str) or text == "":
            return self._error_response("Aucun texte fourni à traiter.", start_time)

        try:
            if mode == "encode":
                output = self.encode(text, separators=allowed_chars)
                return {
                    "status": "ok",
                    "summary": "Encodage des numéros atomiques en symboles chimiques réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": output,
                            "confidence": 1.0,
                            "parameters": {"mode": "encode"},
                            "metadata": {
                                "original_length": len(text),
                            },
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "decode":
                check = self.check_code(text, strict=strict_mode, allowed_chars=allowed_chars, embedded=embedded)
                if not check["is_match"]:
                    return self._error_response(
                        "Aucun symbole chimique valide trouvé" if strict_mode else "Aucun symbole chimique détecté dans le texte",
                        start_time,
                    )

                decoded = self.decode_fragments(text, check["fragments"])
                if not strict_mode and decoded == text:
                    return self._error_response("Aucun symbole chimique n'a pu être décodé", start_time)

                confidence = float(check["score"]) if strict_mode else float(check["score"]) * 0.9

                return {
                    "status": "ok",
                    "summary": f"Décodage des symboles chimiques réussi ({len(check['fragments'])} éléments trouvés)",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": decoded,
                            "confidence": confidence,
                            "parameters": {"mode": "decode", "strict": "strict" if strict_mode else "smooth", "embedded": embedded},
                            "metadata": {
                                "fragments_count": len(check["fragments"]),
                                "detection_score": float(check["score"]),
                            },
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "detect":
                check = self.check_code(text, strict=strict_mode, allowed_chars=allowed_chars, embedded=embedded)
                if not check["is_match"]:
                    return self._error_response("Aucun symbole chimique détecté dans le texte", start_time)

                return {
                    "status": "ok",
                    "summary": f"Détection des symboles chimiques réussie ({len(check['fragments'])} éléments trouvés)",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": "Symboles chimiques détectés",
                            "confidence": float(check["score"]),
                            "parameters": {"mode": "detect", "strict": "strict" if strict_mode else "smooth", "embedded": embedded},
                            "metadata": {
                                "fragments_count": len(check["fragments"]),
                                "fragments": check["fragments"],
                                "detection_score": float(check["score"]),
                            },
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            return self._error_response(f"Mode inconnu : {mode}", start_time)

        except Exception as e:
            return self._error_response(f"Erreur pendant le traitement: {str(e)}", start_time)

    def check_code(self, text: str, strict: bool = False, allowed_chars: Optional[str] = None, embedded: bool = False) -> Dict[str, Any]:
        allowed_chars = self._prepare_allowed_chars(allowed_chars)

        if strict:
            if embedded:
                return self._extract_elements(text, allowed_chars)

            esc_punct = re.escape(allowed_chars)
            words = re.split(f"[{esc_punct}]+", text)
            words = [w for w in words if w]
            if not words:
                return {"is_match": False, "fragments": [], "score": 0.0}

            fragments: List[Dict[str, Any]] = []
            for word in words:
                if word in self.element_to_number:
                    start = text.find(word)
                    fragments.append({"value": word, "start": start, "end": start + len(word)})
                else:
                    return {"is_match": False, "fragments": [], "score": 0.0}

            return {"is_match": bool(fragments), "fragments": fragments, "score": 1.0 if fragments else 0.0}

        return self._extract_elements(text, allowed_chars)

    def _prepare_allowed_chars(self, allowed_chars: Optional[Any]) -> str:
        if allowed_chars is None:
            allowed_chars = DEFAULT_ALLOWED_CHARS
        if isinstance(allowed_chars, list):
            allowed_chars = "".join(allowed_chars)
        allowed_chars = str(allowed_chars)

        for char in NON_BREAKING_WHITESPACES:
            if char not in allowed_chars:
                allowed_chars += char

        return allowed_chars

    def _extract_elements(self, text: str, allowed_chars: Optional[str]) -> Dict[str, Any]:
        allowed_chars = self._prepare_allowed_chars(allowed_chars)
        esc_punct = re.escape(allowed_chars)
        pattern = f"[^{esc_punct}]+"

        fragments: List[Dict[str, Any]] = []
        for match in re.finditer(pattern, text):
            word = match.group(0)
            start, end = match.span()
            if word in self.element_to_number:
                fragments.append({"value": word, "start": start, "end": end})

        return {"is_match": bool(fragments), "fragments": fragments, "score": 1.0 if fragments else 0.0}

    def decode_fragments(self, text: str, fragments: List[Dict[str, Any]]) -> str:
        sorted_fragments = sorted(fragments, key=lambda f: f["start"], reverse=True)
        result = list(text)

        for fragment in sorted_fragments:
            start, end = int(fragment["start"]), int(fragment["end"])
            element = fragment["value"]
            if element in self.element_to_number:
                number = str(self.element_to_number[element])
                result[start:end] = number

        return "".join(result)

    def encode(self, text: str, separators: str) -> str:
        pattern = r"([" + re.escape(separators) + r"]+)|([^" + re.escape(separators) + r"]+)"
        out = ""

        for match in re.finditer(pattern, text):
            part = match.group(0)
            if part.strip(separators) and part.isdigit():
                number = int(part)
                out += self.number_to_element.get(number, part)
            else:
                out += part

        return out

    def _get_plugin_info(self, start_time: float) -> Dict[str, Any]:
        execution_time = (time.time() - start_time) * 1000
        return {"name": self.name, "version": self.version, "execution_time_ms": round(execution_time, 2)}

    def _error_response(self, message: str, start_time: float) -> Dict[str, Any]:
        return {"status": "error", "summary": message, "results": [], "plugin_info": self._get_plugin_info(start_time)}


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return ChemicalElementsPlugin().execute(inputs)
