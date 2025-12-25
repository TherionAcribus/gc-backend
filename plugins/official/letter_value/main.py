from __future__ import annotations

import re
import string
import time
from typing import Any, Dict, List, Optional, Tuple, Union


class LetterValuePlugin:
    def __init__(self) -> None:
        self.name = "letter_value"
        self.version = "1.2.0"
        self.alphabet = string.ascii_uppercase

    def letter_to_value(self, letter: str) -> Optional[int]:
        letter = letter.upper()
        if letter in self.alphabet:
            return self.alphabet.index(letter) + 1
        return None

    def value_to_letter(self, value: Union[str, int]) -> Optional[str]:
        try:
            num = int(value)
            if 1 <= num <= 26:
                return self.alphabet[num - 1]
            return None
        except Exception:
            return None

    def calculate_checksum(self, value: Optional[int]) -> Optional[int]:
        if value is None:
            return None

        str_value = str(value)
        if len(str_value) == 1:
            return value

        sum_digits = sum(int(digit) for digit in str_value)
        if sum_digits > 9:
            return self.calculate_checksum(sum_digits)
        return sum_digits

    def check_code(
        self,
        text: str,
        strict: bool = False,
        allowed_chars: Optional[str] = None,
        embedded: bool = False,
    ) -> Dict[str, Any]:
        if not text:
            return {"is_match": False, "fragments": [], "score": 0.0}

        if allowed_chars is None:
            allowed_chars = " ,.°"

        pattern = r"[A-Za-z]+"

        if embedded:
            matches = re.finditer(pattern, text.upper())
            fragments = [text[m.start() : m.end()] for m in matches]
        else:
            valid_chars = self.alphabet + allowed_chars.upper()
            if all(c in valid_chars for c in text.upper()):
                matches = re.finditer(pattern, text.upper())
                fragments = [text[m.start() : m.end()] for m in matches]
            else:
                fragments = []

        alphabet_chars = sum(1 for c in text.upper() if c in self.alphabet)
        total_chars = len(text) if text else 1
        score = alphabet_chars / total_chars if total_chars > 0 else 0.0

        if strict and score < 0.5:
            fragments = []

        return {"is_match": len(fragments) > 0, "fragments": fragments, "score": float(score)}

    def encode(
        self,
        text: str,
        output_format: str = "standard",
        use_checksum: bool = False,
        brute_force: bool = False,
    ) -> str:
        result: List[str] = []
        current_word = ""

        for char in text:
            if char.upper() in self.alphabet:
                current_word += char
            else:
                if current_word:
                    result.append(self._encode_word(current_word, output_format, use_checksum, brute_force))
                    current_word = ""
                result.append(char)

        if current_word:
            result.append(self._encode_word(current_word, output_format, use_checksum, brute_force))

        return "".join(result)

    def _encode_word(
        self,
        word: str,
        output_format: str,
        use_checksum: bool,
        brute_force: bool,
    ) -> str:
        word_result_parts: List[str] = []
        for letter in word:
            value = self.letter_to_value(letter)
            if value is None:
                word_result_parts.append(letter)
                continue

            if brute_force:
                checksum = self.calculate_checksum(value)
                word_result_parts.append(f"{value}/{checksum}")
            elif use_checksum:
                word_result_parts.append(str(self.calculate_checksum(value)))
            else:
                word_result_parts.append(str(value))

        return " ".join(word_result_parts)

    def decode(self, text: str, input_format: str = "standard") -> str:
        cleaned_text = text.upper()
        result = ""
        current_number = ""

        for char in cleaned_text:
            if char.isdigit():
                current_number += char
            else:
                if current_number:
                    letter = self.value_to_letter(int(current_number))
                    result += letter if letter else current_number
                    current_number = ""

                if not char.isalpha():
                    result += char

        if current_number:
            letter = self.value_to_letter(int(current_number))
            result += letter if letter else current_number

        return result.strip()

    def _decode_numeric_sequence(self, seq: str) -> Optional[str]:
        """Décode une suite de chiffres (sans séparateur) en lettres A-Z.

        L'encodage legacy concatène les valeurs (1-26) sans séparateurs.
        On reconstruit une segmentation valide (1..26) de manière déterministe.
        """

        s = re.sub(r"\s+", "", seq)
        if not s or not s.isdigit():
            return None

        memo: Dict[int, Optional[str]] = {}

        def solve(i: int) -> Optional[str]:
            if i == len(s):
                return ""
            if i in memo:
                return memo[i]

            if s[i] == "0":
                memo[i] = None
                return None

            # Priorité aux nombres à 2 chiffres si possible (10-26) pour favoriser 19, 20, 21, etc.
            if i + 1 < len(s):
                two = int(s[i : i + 2])
                if 10 <= two <= 26:
                    rest = solve(i + 2)
                    if rest is not None:
                        letter = self.value_to_letter(two)
                        memo[i] = (letter or "") + rest if letter else None
                        return memo[i]

            one = int(s[i])
            if 1 <= one <= 9:
                rest = solve(i + 1)
                if rest is not None:
                    letter = self.value_to_letter(one)
                    memo[i] = (letter or "") + rest if letter else None
                    return memo[i]

            memo[i] = None
            return None

        return solve(0)

    def _decode_numbers_in_text(self, text: str, strict_mode: bool) -> str:
        """Décode toutes les séquences numériques présentes dans le texte."""

        def repl_spaced(match: re.Match) -> str:
            segment = match.group(0)
            tokens = re.split(r"\s+", segment.strip())
            letters: List[str] = []
            for tok in tokens:
                if not tok.isdigit():
                    if strict_mode:
                        raise ValueError(f"Séquence numérique invalide: {segment}")
                    return segment
                letter = self.value_to_letter(tok)
                if letter is None:
                    if strict_mode:
                        raise ValueError(f"Séquence numérique invalide: {segment}")
                    return segment
                letters.append(letter)
            return "".join(letters)

        # 1) Cas lisible : nombres séparés par des espaces (ex: "3 1 20")
        # On décode uniquement si on a au moins 2 nombres, pour ne pas casser des coordonnées comme "N 14° ...".
        text = re.sub(r"(?:\d{1,2})(?:[ \t]+\d{1,2})+", repl_spaced, text)

        def repl_concat(match: re.Match) -> str:
            seq = match.group(0)
            decoded = self._decode_numeric_sequence(seq)
            if decoded is None:
                if strict_mode:
                    raise ValueError(f"Séquence numérique invalide: {seq}")
                return seq
            return decoded

        # 2) Cas legacy : valeurs concaténées (ex: "3120")
        return re.sub(r"\d{2,}", repl_concat, text)

    def _looks_like_numeric_code(self, text: str) -> bool:
        if not text:
            return False

        if re.search(r"(?:\d{1,2})(?:\s+\d{1,2})+", text):
            return True

        for m in re.finditer(r"\d{2,}", text):
            if self._decode_numeric_sequence(m.group(0)) is not None:
                return True

        return False

    def format_coordinates(self, text: str) -> str:
        pattern = r"(\d+)°\s*(\d+)\.(\d+)"
        match = re.search(pattern, text)
        if match:
            degrees = match.group(1)
            minutes = match.group(2)
            seconds = match.group(3)
            return f"{degrees}° {minutes}.{seconds}"
        return text

    def get_bruteforce_results(
        self,
        text: str,
        output_format: str,
        allowed_chars: str,
        embedded: bool,
        strict_mode: bool,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        test_params = [
            {"use_checksum": False, "confidence": 0.9, "description": "Valeurs standard (A=1, B=2...)"},
            {"use_checksum": True, "confidence": 0.7, "description": "Valeurs avec checksum"},
        ]

        for idx, params in enumerate(test_params):
            use_checksum = bool(params["use_checksum"])
            confidence = float(params["confidence"])
            description = str(params["description"])

            check_result = self.check_code(text, strict_mode, allowed_chars, embedded)
            if not check_result["is_match"]:
                continue

            result_text = text
            sorted_fragments = sorted(check_result["fragments"], key=len, reverse=True)

            for fragment in sorted_fragments:
                encoded_fragment = self.encode(fragment, output_format, use_checksum, False)
                pattern = "".join(f"[{c.upper()}{c.lower()}]" for c in fragment)
                result_text = re.sub(pattern, encoded_fragment, result_text)

            formatted = self.format_coordinates(result_text)
            adjusted_confidence = confidence * float(check_result["score"])

            results.append(
                {
                    "id": f"result_{idx + 1}",
                    "text_output": formatted,
                    "confidence": float(adjusted_confidence),
                    "parameters": {"use_checksum": use_checksum, "output_format": output_format},
                    "metadata": {
                        "fragments": check_result["fragments"],
                        "description": description,
                        "detection_score": float(check_result["score"]),
                    },
                }
            )

        return results

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

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        allowed_chars = str(inputs.get("allowed_chars", " ,.°"))
        embedded = self._parse_bool(inputs.get("embedded", True), default=True)
        output_format = str(inputs.get("format", "combined")).lower()
        use_checksum = self._parse_bool(inputs.get("checksum", False), default=False)
        bruteforce = self._parse_bool(inputs.get("bruteforce", False), default=False) or self._parse_bool(
            inputs.get("brute_force", False),
            default=False,
        )

        if not isinstance(text, str) or text == "":
            return self._error_response("Le texte d'entrée est vide", start_time)

        try:
            if bruteforce and mode == "decode":
                results = self.get_bruteforce_results(text, output_format, allowed_chars, embedded, strict_mode)
                if not results:
                    return self._error_response("Aucun code letter value détecté", start_time)

                return {
                    "status": "ok",
                    "summary": f"{len(results)} résultat(s) généré(s) en mode bruteforce",
                    "results": results,
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "encode":
                encoded = self.encode(text, output_format, use_checksum, False)
                return {
                    "status": "ok",
                    "summary": "Encodage réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": encoded,
                            "confidence": 1.0,
                            "parameters": {"use_checksum": use_checksum, "output_format": output_format},
                            "metadata": {},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "decode":
                # Si on reçoit une sortie encodée (chiffres), on tente de reconstituer le texte.
                # Si ça ne change rien, on retombe sur le comportement legacy (lettres -> nombres).
                if self._looks_like_numeric_code(text):
                    decoded_text = self._decode_numbers_in_text(text, strict_mode=strict_mode)
                    if decoded_text != text:
                        formatted = self.format_coordinates(decoded_text)
                        return {
                            "status": "ok",
                            "summary": "Décodage réussi",
                            "results": [
                                {
                                    "id": "result_1",
                                    "text_output": formatted,
                                    "confidence": 0.8,
                                    "parameters": {"output_format": output_format},
                                    "metadata": {"numeric": True},
                                }
                            ],
                            "plugin_info": self._get_plugin_info(start_time),
                        }

                # Sinon, comportement legacy : repérer des fragments de lettres et les convertir en chiffres.
                check_result = self.check_code(text, strict_mode, allowed_chars, embedded)
                if not check_result["is_match"]:
                    return self._error_response("Aucun code letter value détecté", start_time)

                result_text = text
                sorted_fragments = sorted(check_result["fragments"], key=len, reverse=True)

                for fragment in sorted_fragments:
                    encoded_fragment = self.encode(fragment, output_format, use_checksum, False)
                    pattern = "".join(f"[{c.upper()}{c.lower()}]" for c in fragment)
                    result_text = re.sub(pattern, encoded_fragment, result_text)

                formatted = self.format_coordinates(result_text)

                return {
                    "status": "ok",
                    "summary": "Décodage réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": formatted,
                            "confidence": float(check_result["score"]),
                            "parameters": {"use_checksum": use_checksum, "output_format": output_format},
                            "metadata": {"fragments": check_result["fragments"]},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "detect":
                if self._looks_like_numeric_code(text):
                    digit_chars = sum(1 for c in text if c.isdigit())
                    total_chars = len(text) if text else 1
                    score = digit_chars / total_chars if total_chars else 0.0
                    is_match = score >= 0.2
                    check_result = {"fragments": re.findall(r"\d+", text)}
                else:
                    check_result = self.check_code(text, strict_mode, allowed_chars, embedded)
                    score = float(check_result.get("score", 0.0) or 0.0)
                    is_match = bool(check_result.get("is_match"))

                summary = "Code letter value détecté" if is_match else "Aucun code letter value détecté"
                details = f"{summary} (score: {score:.2f})"

                return {
                    "status": "ok",
                    "summary": summary,
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": details,
                            "confidence": score,
                            "parameters": {"strict": "strict" if strict_mode else "smooth", "embedded": embedded},
                            "metadata": {"fragments": check_result.get("fragments", [])},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            return self._error_response(f"Mode inconnu : {mode}", start_time)

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
    return LetterValuePlugin().execute(inputs)
