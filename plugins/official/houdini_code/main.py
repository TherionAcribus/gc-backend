from __future__ import annotations

import re
import time
from typing import Any, Dict, List


class HoudiniCodePlugin:
    _number_to_word = {
        1: "Pray",
        2: "Answer",
        3: "Say",
        4: "Now",
        5: "Tell",
        6: "Please",
        7: "Speak",
        8: "Quickly",
        9: "Look",
        0: "Be Quick",
    }

    _letter_to_word = {chr(ord("A") + (n - 1)): w for n, w in _number_to_word.items() if n != 0}
    _letter_to_word["J"] = "Be Quick"

    _word_to_number: Dict[str, int] = {w.lower(): n for n, w in _number_to_word.items()}
    _word_to_number["be quick"] = 0

    _word_to_letter: Dict[str, str] = {w.lower(): l for l, w in _letter_to_word.items()}
    _word_to_letter["be quick"] = "J"

    def __init__(self) -> None:
        self.name = "houdini_code"
        self.version = "1.0.0"

    def encode(self, text: str, strict: str = "smooth") -> str:
        if strict == "strict":
            self._validate_encode_strict(text)

        tokens: List[str] = []
        i = 0
        text = text.strip()
        while i < len(text):
            c = text[i]
            if c.isdigit():
                if c == "1" and i + 1 < len(text) and text[i + 1] == "0":
                    tokens.append(self._number_to_word[0])
                    i += 2
                    continue

                num = int(c)
                if num in self._number_to_word:
                    tokens.append(self._number_to_word[num])
                else:
                    tokens.append(c)

            else:
                uc = c.upper()
                if uc in self._letter_to_word:
                    tokens.append(self._letter_to_word[uc])
                else:
                    tokens.append(c)

            i += 1

        return " ".join(tokens)

    def _decode(self, text: str, as_letters: bool, strict: str = "smooth") -> str:
        words = self._tokenize_words(text)
        if strict == "strict":
            self._validate_decode_strict(words)

        out: List[str] = []
        for w in words:
            key = w.lower()
            if key in self._word_to_number:
                if as_letters:
                    out.append(self._word_to_letter.get(key, "?"))
                else:
                    out.append(str(self._word_to_number[key]))
            else:
                out.append(w)
        return "".join(out)

    @staticmethod
    def _tokenize_words(text: str) -> List[str]:
        text = re.sub(r"\s+", " ", text.strip())
        if not text:
            return []

        tokens = text.split(" ")
        merged: List[str] = []
        skip_next = False

        for idx, tok in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue

            if tok.lower() == "be" and idx + 1 < len(tokens) and tokens[idx + 1].lower() == "quick":
                merged.append("Be Quick")
                skip_next = True
            else:
                merged.append(tok)

        return merged

    def _validate_encode_strict(self, text: str) -> None:
        for c in text.strip():
            if c.isdigit():
                continue
            if c.upper() in "ABCDEFGHIJ":
                continue
            if c.isspace():
                continue
            raise ValueError(f"Caractère non autorisé en mode strict: {repr(c)}")

    def _validate_decode_strict(self, words: List[str]) -> None:
        for w in words:
            if w.lower() in self._word_to_number:
                continue
            raise ValueError(f"Mot Houdini inconnu en mode strict: {w}")

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        output_format = str(inputs.get("output_format", "numbers")).lower()
        strict = str(inputs.get("strict", "smooth")).lower()

        bruteforce = bool(inputs.get("bruteforce", False)) or bool(inputs.get("brute_force", False))

        if not isinstance(text, str) or text == "":
            return self._error_response("Aucun texte fourni", start_time)
        if strict not in {"strict", "smooth"}:
            return self._error_response(f"Mode strict invalide: {strict}", start_time)

        try:
            if mode == "encode":
                output = self.encode(text, strict=strict)
                return {
                    "status": "ok",
                    "summary": "Encodage Houdini réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": output,
                            "confidence": 1.0,
                            "parameters": {"mode": "encode", "strict": strict},
                            "metadata": {"processed_chars": len(text)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "decode":
                if bruteforce:
                    numbers_out = self._decode(text, as_letters=False, strict=strict)
                    letters_out = self._decode(text, as_letters=True, strict=strict)

                    return {
                        "status": "ok",
                        "summary": "Décodage bruteforce (chiffres + lettres) réussi",
                        "results": [
                            {
                                "id": "result_1",
                                "text_output": numbers_out,
                                "confidence": 0.6,
                                "parameters": {"mode": "decode", "output_format": "numbers", "strict": strict},
                                "metadata": {},
                            },
                            {
                                "id": "result_2",
                                "text_output": letters_out,
                                "confidence": 0.4,
                                "parameters": {"mode": "decode", "output_format": "letters", "strict": strict},
                                "metadata": {},
                            },
                        ],
                        "plugin_info": self._get_plugin_info(start_time),
                    }

                as_letters = output_format == "letters"
                output = self._decode(text, as_letters=as_letters, strict=strict)
                return {
                    "status": "ok",
                    "summary": "Décodage Houdini réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": output,
                            "confidence": 0.8,
                            "parameters": {"mode": "decode", "output_format": output_format, "strict": strict},
                            "metadata": {},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            return self._error_response(f"Mode inconnu: {mode}", start_time)

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
    return HoudiniCodePlugin().execute(inputs)
