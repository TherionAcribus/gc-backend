from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional


class NakNakCodePlugin:
    def __init__(self) -> None:
        self.name = "nak_nak_code"
        self.version = "1.0.0"

        try:
            from gc_backend.plugins.scoring import score_text

            self._score_text = score_text
            self._scoring_available = True
        except Exception:
            self._score_text = None
            self._scoring_available = False

        self.encode_table = {
            "0": "Nak",
            "1": "Nanak",
            "2": "Nananak",
            "3": "Nanananak",
            "4": "Nak?",
            "5": "nak?",
            "6": "Naknak",
            "7": "Naknaknak",
            "8": "Nak.",
            "9": "Naknak.",
            "A": "Naknaknaknak",
            "B": "nanak",
            "C": "naknak",
            "D": "nak!",
            "E": "nak.",
            "F": "naknaknak",
            "a": "Naknaknaknak",
            "b": "nanak",
            "c": "naknak",
            "d": "nak!",
            "e": "nak.",
            "f": "naknaknak",
        }

        self.decode_table: Dict[str, str] = {}
        for key, value in self.encode_table.items():
            if value not in self.decode_table or (key.lower() == key and key.isalpha()):
                self.decode_table[value] = key

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "encode")).lower()
        text = inputs.get("text", "")
        strict_mode = str(inputs.get("strict", "")).lower() == "strict"
        allowed_chars = inputs.get("allowed_chars", " \t\r\n.:;,_-°")
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
            },
        }

        if not isinstance(text, str) or not text:
            result["status"] = "error"
            result["summary"]["message"] = "Aucun texte fourni à traiter."
            result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return result

        try:
            if mode == "encode":
                encoded = self.encode(text)
                response_result = {
                    "id": "result_1",
                    "text_output": encoded,
                    "confidence": 1.0,
                    "parameters": {"mode": mode},
                    "metadata": {"processed_chars": len(text)},
                }

                result["results"].append(response_result)
                result["summary"]["best_result_id"] = "result_1"
                result["summary"]["total_results"] = 1
                result["summary"]["message"] = "Encodage réussi"

            elif mode == "decode":
                check_result = self.check_code(
                    text,
                    strict=strict_mode,
                    allowed_chars=allowed_chars,
                    embedded=embedded,
                )

                if check_result["is_match"]:
                    if embedded:
                        decoded_text = self.decode_fragments(text, check_result["fragments"])
                    else:
                        decoded_text = self.decode(text)

                    scoring_result = None
                    if enable_scoring:
                        scoring_result = self._get_text_score(decoded_text, context)
                        if scoring_result and "score" in scoring_result:
                            confidence = float(scoring_result.get("score", 0.5))
                        else:
                            confidence = check_result["score"]
                            scoring_result = None
                    else:
                        confidence = check_result["score"]

                    response_result = {
                        "id": "result_1",
                        "text_output": decoded_text,
                        "confidence": confidence,
                        "parameters": {
                            "mode": mode,
                            "strict": "strict" if strict_mode else "smooth",
                            "embedded": embedded,
                        },
                        "metadata": {
                            "processed_chars": len(text),
                            "fragments_found": len(check_result["fragments"]),
                        },
                    }

                    if scoring_result:
                        response_result["scoring"] = scoring_result

                    result["results"].append(response_result)
                    result["summary"]["best_result_id"] = "result_1"
                    result["summary"]["total_results"] = 1
                    result["summary"]["message"] = "Décodage réussi"
                else:
                    result["status"] = "error"
                    result["summary"]["message"] = "Aucun code Nak Nak valide trouvé dans le texte."
            else:
                result["status"] = "error"
                result["summary"]["message"] = f"Mode non reconnu: {mode}. Utilisez 'encode' ou 'decode'."

        except Exception as exc:
            result["status"] = "error"
            result["summary"]["message"] = f"Erreur lors du traitement: {exc}"

        result["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return result

    def check_code(
        self,
        text: str,
        *,
        strict: bool = False,
        allowed_chars: Optional[str] = None,
        embedded: bool = False,
    ) -> Dict[str, Any]:
        if allowed_chars is not None and isinstance(allowed_chars, list):
            allowed_chars = "".join(allowed_chars)

        if allowed_chars is None:
            allowed_chars = " \t\r\n.:;,_-°"

        nak_pattern = r"(?:Na(?:k|\?)\??)"

        if strict:
            if embedded:
                return self._extract_nak_nak_fragments(text, allowed_chars)

            esc_punct = re.escape(allowed_chars)
            words = re.split(f"[{esc_punct}]+", text)
            valid_words: List[str] = []

            for word in words:
                if not word:
                    continue
                if word in self.decode_table or re.match(f"^({nak_pattern})+$", word, re.IGNORECASE):
                    valid_words.append(word)

            if not valid_words:
                return {"is_match": False, "fragments": [], "score": 0.0}

            fragments = []
            for word in valid_words:
                start = text.find(word)
                if start != -1:
                    fragments.append({"value": word, "start": start, "end": start + len(word)})

            return {
                "is_match": True,
                "fragments": fragments,
                "score": 1.0 if fragments else 0.0,
            }

        return self._extract_nak_nak_fragments(text, allowed_chars)

    def _extract_nak_nak_fragments(self, text: str, allowed_chars: str) -> Dict[str, Any]:
        esc_punct = re.escape(allowed_chars)
        fragments: List[Dict[str, Any]] = []

        for nak_code in self.decode_table.keys():
            for match in re.finditer(re.escape(nak_code), text, re.IGNORECASE):
                start, end = match.span()
                fragments.append({"value": text[start:end], "start": start, "end": end})

        words = re.split(f"[{esc_punct}]+", text)
        for word in words:
            if not word:
                continue
            if re.search(r"Na[k?]", word, re.IGNORECASE):
                start = text.find(word)
                if start != -1 and not any(f["start"] <= start < f["end"] for f in fragments):
                    fragments.append({"value": word, "start": start, "end": start + len(word)})

        if fragments:
            fragments = sorted(fragments, key=lambda x: x["start"])
            i = 0
            while i < len(fragments) - 1:
                if fragments[i]["end"] >= fragments[i + 1]["start"]:
                    fragments[i]["end"] = max(fragments[i]["end"], fragments[i + 1]["end"])
                    fragments[i]["value"] = text[fragments[i]["start"] : fragments[i]["end"]]
                    fragments.pop(i + 1)
                else:
                    i += 1

        score = 1.0 if fragments else 0.0
        return {"is_match": bool(fragments), "fragments": fragments, "score": score}

    def decode_fragments(self, text: str, fragments: List[Dict[str, Any]]) -> str:
        sorted_fragments = sorted(fragments, key=lambda x: x["start"])
        result: List[str] = []
        last_pos = 0

        for frag in sorted_fragments:
            result.append(text[last_pos : frag["start"]])
            result.append(self.decode(frag["value"]))
            last_pos = frag["end"]

        result.append(text[last_pos:])
        return "".join(result)

    def encode(self, text: str) -> str:
        result: List[str] = []
        for char in text:
            hex_code = format(ord(char), "x")
            nak_codes = [self.encode_table.get(digit, digit) for digit in hex_code]
            result.append(" ".join(nak_codes))
        return " ".join(result)

    def decode(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        segments = re.split(r"\s+", text)
        hex_chars: List[str] = []
        current_hex = ""

        for segment in segments:
            hex_digit = self.decode_table.get(segment)
            if hex_digit is not None:
                current_hex += hex_digit
                if len(current_hex) == 2:
                    try:
                        hex_chars.append(chr(int(current_hex, 16)))
                    except ValueError:
                        pass
                    current_hex = ""
            else:
                if current_hex:
                    if len(current_hex) == 1:
                        try:
                            hex_chars.append(chr(int(current_hex, 16)))
                        except ValueError:
                            pass
                    current_hex = ""
                hex_chars.append("?")

        if current_hex:
            try:
                hex_chars.append(chr(int(current_hex, 16)))
            except ValueError:
                pass

        return "".join(hex_chars)

    def _clean_text_for_scoring(self, text: str) -> str:
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _get_text_score(self, text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not self._scoring_available or not self._score_text:
            return None
        cleaned_text = self._clean_text_for_scoring(text)
        try:
            return self._score_text(cleaned_text, context=context or {})
        except Exception:
            return None


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return NakNakCodePlugin().execute(inputs)
