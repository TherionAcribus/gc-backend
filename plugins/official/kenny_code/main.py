from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional


class KennyCodePlugin:
    def __init__(self) -> None:
        self.name = "kenny_code"
        self.version = "1.3.0"

        self.encode_table: Dict[str, str] = {
            "a": "mmm",
            "b": "mmp",
            "c": "mmf",
            "d": "mpm",
            "e": "mpp",
            "f": "mpf",
            "g": "mfm",
            "h": "mfp",
            "i": "mff",
            "j": "pmm",
            "k": "pmp",
            "l": "pmf",
            "m": "ppm",
            "n": "ppp",
            "o": "ppf",
            "p": "pfm",
            "q": "pfp",
            "r": "pff",
            "s": "fmm",
            "t": "fmp",
            "u": "fmf",
            "v": "fpm",
            "w": "fpp",
            "x": "fpf",
            "y": "ffm",
            "z": "ffp",
        }
        self.decode_table: Dict[str, str] = {v: k for k, v in self.encode_table.items()}

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        strict = str(inputs.get("strict", "smooth")).lower()
        embedded = bool(inputs.get("embedded", False))
        allowed_chars = inputs.get("allowed_chars", " \t\r\n.:;,_-°")

        if not isinstance(text, str) or text == "":
            return self._error_response("Aucun texte fourni", start_time)
        if strict not in {"strict", "smooth"}:
            return self._error_response(f"Mode strict invalide: {strict}", start_time)
        if not isinstance(allowed_chars, str):
            return self._error_response("allowed_chars doit être une chaîne", start_time)

        try:
            if mode == "encode":
                output = self.encode(text)
                return {
                    "status": "ok",
                    "summary": "Encodage Kenny réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": output,
                            "confidence": 1.0,
                            "parameters": {"mode": "encode"},
                            "metadata": {"processed_chars": len(text)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "decode":
                strict_mode = strict == "strict"

                if strict_mode:
                    check = self.check_code(text, strict=True, allowed_chars=allowed_chars, embedded=embedded)
                    if not check["is_match"]:
                        return self._error_response("Code Kenny invalide en mode strict", start_time)

                    decoded = self.decode_fragments(text, check["fragments"])
                    return {
                        "status": "ok",
                        "summary": "Décodage Kenny réussi (strict)",
                        "results": [
                            {
                                "id": "result_1",
                                "text_output": decoded,
                                "confidence": 0.9,
                                "parameters": {"mode": "decode", "strict": "strict", "embedded": embedded},
                                "metadata": {
                                    "fragments_count": len(check["fragments"]),
                                    "full_match": bool(check.get("full_match")),
                                },
                            }
                        ],
                        "plugin_info": self._get_plugin_info(start_time),
                    }

                check = self.check_code(text, strict=False, allowed_chars=allowed_chars, embedded=embedded)
                if not check["is_match"]:
                    return self._error_response("Aucun code Kenny détecté dans le texte", start_time)

                decoded = self.decode_fragments(text, check["fragments"])
                if decoded == text:
                    return self._error_response("Aucun code Kenny n'a pu être décodé", start_time)

                fragments_text_length = sum(len(frag.get("value", "")) for frag in check["fragments"])
                coverage_ratio = fragments_text_length / len(text) if text else 0.0

                confidence = 0.5 + (coverage_ratio * 0.4)
                if len(check["fragments"]) > 3:
                    confidence -= 0.1
                confidence = max(0.1, min(0.9, confidence))

                return {
                    "status": "ok",
                    "summary": f"Décodage Kenny réussi (smooth, {len(check['fragments'])} fragment(s))",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": decoded,
                            "confidence": float(confidence),
                            "parameters": {"mode": "decode", "strict": "smooth", "embedded": embedded},
                            "metadata": {
                                "fragments_count": len(check["fragments"]),
                                "coverage_ratio": float(coverage_ratio),
                                "fragments": [
                                    {
                                        "start": f.get("start"),
                                        "end": f.get("end"),
                                        "value": f.get("value"),
                                    }
                                    for f in check["fragments"]
                                ],
                            },
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            if mode == "detect":
                check = self.check_code(text, strict=(strict == "strict"), allowed_chars=allowed_chars, embedded=embedded)
                score = float(check.get("score", 0.0) or 0.0)
                is_match = bool(check.get("is_match"))

                return {
                    "status": "ok",
                    "summary": "Code Kenny détecté" if is_match else "Aucun code Kenny détecté",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": f"Probabilité Kenny: {score:.2%}",
                            "confidence": score,
                            "parameters": {"mode": "detect", "strict": strict, "embedded": embedded},
                            "metadata": {
                                "is_match": is_match,
                                "detection_score": score,
                                "fragments_count": len(check.get("fragments") or []),
                            },
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            return self._error_response(f"Mode inconnu: {mode}", start_time)

        except Exception as e:
            return self._error_response(str(e), start_time)

    def encode(self, text: str) -> str:
        result: List[str] = []
        for char in text.lower():
            if char in self.encode_table:
                result.append(self.encode_table[char])
            elif char.isspace():
                result.append(" ")
            else:
                result.append(char)
        return "".join(result)

    def decode(self, text: str) -> str:
        result: List[str] = []
        i = 0
        current_group: List[str] = []

        while i < len(text):
            if text[i].isspace():
                if current_group:
                    result.append(self._decode_group("".join(current_group).lower()))
                    current_group = []
                result.append(" ")
                i += 1
            else:
                current_group.append(text[i])
                if len(current_group) == 3:
                    result.append(self._decode_group("".join(current_group).lower()))
                    current_group = []
                i += 1

        if current_group:
            result.append(self._decode_group("".join(current_group).lower()))

        return "".join(result)

    def _decode_group(self, group: str) -> str:
        return self.decode_table.get(group, "?")

    def check_code(
        self,
        text: str,
        *,
        strict: bool = False,
        allowed_chars: Optional[str] = None,
        embedded: bool = False,
    ) -> Dict[str, Any]:
        if allowed_chars is None:
            allowed_chars = " \t\r\n.:;,_-°"

        if not text:
            return {"is_match": False, "fragments": [], "score": 0.0}

        if strict:
            if embedded:
                return self._extract_kenny_fragments(text, allowed_chars)

            esc_punct = re.escape(allowed_chars)
            pattern_str = f"^[mpf{esc_punct}]*$"
            if not re.match(pattern_str, text.lower()):
                return {"is_match": False, "fragments": [], "score": 0.0}

            kenny_chars_found = re.sub(f"[{esc_punct}]", "", text.lower())
            if not kenny_chars_found:
                return {"is_match": False, "fragments": [], "score": 0.0}

            clean_text = re.sub(f"[{esc_punct}]", "", text.lower())
            valid_triplets = []
            for i in range(0, len(clean_text), 3):
                if i + 3 <= len(clean_text):
                    triplet = clean_text[i : i + 3]
                    if triplet in self.decode_table:
                        valid_triplets.append(triplet)

            if not valid_triplets:
                return {"is_match": False, "fragments": [], "score": 0.0}

            stripped_text = text.strip(allowed_chars)
            start = text.find(stripped_text)
            fragment = {"value": stripped_text, "start": start, "end": start + len(stripped_text)}

            return {
                "is_match": True,
                "fragments": [fragment],
                "score": 1.0,
                "full_match": bool(start == 0 and len(stripped_text) == len(text)),
            }

        return self._extract_kenny_fragments(text, allowed_chars)

    def _extract_kenny_fragments(self, text: str, allowed_chars: str) -> Dict[str, Any]:
        kenny_chars = "mpf"
        esc_punct = re.escape(allowed_chars)
        pattern = f"([^{esc_punct}]+)|([{esc_punct}]+)"
        fragments: List[Dict[str, Any]] = []

        for m in re.finditer(pattern, text.lower()):
            block = m.group(0)
            start, end = m.span()

            if re.match(f"^[{esc_punct}]+$", block):
                continue

            valid_triplets = []
            for i in range(0, len(block), 3):
                if i + 3 <= len(block):
                    triplet = block[i : i + 3]
                    if triplet in self.decode_table:
                        valid_triplets.append(triplet)

            if valid_triplets:
                fragments.append({"value": text[start:end], "start": start, "end": end})

        score = 1.0 if fragments else 0.0
        return {"is_match": bool(fragments), "fragments": fragments, "score": score}

    def decode_fragments(self, text: str, fragments: List[Dict[str, Any]]) -> str:
        sorted_fragments = sorted(fragments, key=lambda x: x.get("start", 0))
        result: List[str] = []
        last_pos = 0

        for frag in sorted_fragments:
            start = int(frag.get("start", 0))
            end = int(frag.get("end", 0))
            value = str(frag.get("value", ""))
            result.append(text[last_pos:start])
            result.append(self.decode(value))
            last_pos = end

        result.append(text[last_pos:])
        return "".join(result)

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
    return KennyCodePlugin().execute(inputs)
