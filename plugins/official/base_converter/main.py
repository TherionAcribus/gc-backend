"""Plugin Base Converter pour MysterAI.

Ce plugin convertit des valeurs entre des bases numériques (2, 8, 10, 16, 36, 64)
et ASCII.

Fonctionnalités:
- Conversion ciblée source_base -> target_base
- Mode `autodetect`/`brute_force`: teste toutes les combinaisons utiles et classe les résultats
- Modes `strict`/`smooth` + `embedded` pour convertir des fragments dans un texte
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from typing import Any, Dict, List, Optional, Tuple


class BaseConverterPlugin:
    """Convertisseur de bases numériques et ASCII."""

    def __init__(self) -> None:
        self.name = "base_converter"
        self.version = "1.7.0"

        self._base_chars: Dict[str, str] = {
            "2": "01",
            "8": "01234567",
            "10": "0123456789",
            "16": "0123456789ABCDEFabcdef",
            "36": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            "64": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=",
        }
        self._supported_bases: List[str] = ["2", "8", "10", "16", "36", "64", "ascii"]

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin."""
        start_time = time.time()

        text = inputs.get("text")
        if (text is None or text == "") and inputs.get("input_text"):
            text = inputs.get("input_text")
        if (text is None or text == "") and inputs.get("input_value"):
            text = inputs.get("input_value")
        if text is None:
            text = ""

        mode = str(inputs.get("mode", "decode")).lower()
        source_base = str(inputs.get("source_base", "16")).lower()
        target_base = str(inputs.get("target_base", "ascii")).lower()
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        embedded = bool(inputs.get("embedded", False))
        enable_gps_detection = bool(inputs.get("enable_gps_detection", True))

        brute_force = bool(inputs.get("brute_force", False)) or bool(inputs.get("bruteforce", False))

        if not text:
            return self._error_response("Aucune valeur fournie à convertir.", start_time)

        # Mode encode/decode : définir des defaults identiques à l'ancien plugin.
        if mode == "encode":
            if not source_base:
                source_base = "ascii"
            if not target_base:
                target_base = "16"
        elif mode == "decode":
            if not source_base:
                source_base = "16"
            if not target_base:
                target_base = "ascii"

        if mode == "autodetect" or brute_force:
            results = self._bruteforce_convert(text, strict_mode=strict_mode, embedded=embedded, enable_gps_detection=enable_gps_detection)
            if not results:
                return self._error_response("Aucune conversion valide trouvée.", start_time)
            return {
                "status": "ok",
                "summary": f"{len(results)} conversion(s) possible(s) trouvée(s).",
                "results": results,
                "plugin_info": self._get_plugin_info(start_time),
            }

        if source_base == target_base:
            return {
                "status": "ok",
                "summary": "Les bases source et cible sont identiques, aucune conversion nécessaire.",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": text,
                        "confidence": 1.0,
                        "parameters": {"source_base": source_base, "target_base": target_base},
                        "metadata": {"fragments_count": 1},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if source_base not in self._supported_bases or target_base not in self._supported_bases:
            return self._error_response("Base source ou cible non supportée.", start_time)

        check = self._check_code(text, base=source_base, strict=strict_mode, embedded=embedded)
        if not check["is_match"]:
            return self._error_response(f"La valeur fournie n'est pas valide dans la base {source_base}.", start_time)

        if embedded or len(check["fragments"]) > 1:
            converted_text = self._decode_fragments(text, fragments=check["fragments"], source_base=source_base, target_base=target_base)
            return {
                "status": "ok",
                "summary": f"{len(check['fragments'])} fragment(s) converti(s) de base {source_base} vers base {target_base}.",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": converted_text,
                        "confidence": float(check.get("score", 0.5)),
                        "parameters": {"source_base": source_base, "target_base": target_base, "fragments_count": len(check["fragments"])},
                        "metadata": {"fragments": [f["value"] for f in check["fragments"]]},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        converted = self._convert_base(text, source_base=source_base, target_base=target_base)
        if converted.startswith("Erreur:"):
            return self._error_response(converted, start_time)

        return {
            "status": "ok",
            "summary": f"Conversion réussie de base {source_base} vers base {target_base}.",
            "results": [
                {
                    "id": "result_1",
                    "text_output": converted,
                    "confidence": 1.0,
                    "parameters": {"source_base": source_base, "target_base": target_base},
                    "metadata": {"fragments_count": 1},
                }
            ],
            "plugin_info": self._get_plugin_info(start_time),
        }

    def _check_code(self, text: str, base: str, strict: bool, embedded: bool, allowed_chars: Optional[str] = None) -> Dict[str, Any]:
        if base == "ascii":
            return {"is_match": True, "fragments": [{"value": text, "start": 0, "end": len(text)}], "score": 1.0}

        if allowed_chars is None:
            allowed_chars = " \t\r\n.:;,_-°"

        valid_chars = self._base_chars.get(base, "")
        esc_punct = re.escape(allowed_chars)

        if strict and not embedded:
            pattern_str = f"^[{re.escape(valid_chars)}{esc_punct}]*$"
            if not re.match(pattern_str, text):
                return {"is_match": False, "fragments": [], "score": 0.0}

            fragments = self._extract_base_fragments(text, valid_chars=valid_chars, allowed_chars=allowed_chars)
            if not fragments:
                return {"is_match": False, "fragments": [], "score": 0.0}

            if base == "64":
                for frag in fragments:
                    if len(frag["value"]) % 4 != 0:
                        return {"is_match": False, "fragments": [], "score": 0.0}

            return {"is_match": True, "fragments": fragments, "score": 1.0}

        # smooth or embedded strict: extraire des fragments.
        fragments = self._extract_base_fragments(text, valid_chars=valid_chars, allowed_chars=allowed_chars)
        return {"is_match": bool(fragments), "fragments": fragments, "score": 1.0 if fragments else 0.0}

    def _extract_base_fragments(self, text: str, valid_chars: str, allowed_chars: str) -> List[Dict[str, Any]]:
        esc_punct = re.escape(allowed_chars)
        pattern = f"([^{esc_punct}]+)|([{esc_punct}]+)"
        fragments: List[Dict[str, Any]] = []

        valid_set = set(valid_chars)

        for m in re.finditer(pattern, text):
            block = m.group(0)
            start, end = m.span()

            if re.match(f"^[{esc_punct}]+$", block):
                continue

            if block and all(c in valid_set for c in block):
                fragments.append({"value": text[start:end], "start": start, "end": end})

        return fragments

    def _convert_base(self, value: str, source_base: str, target_base: str) -> str:
        try:
            if source_base == "ascii":
                return self._ascii_to_target(value, target_base)

            if target_base == "ascii":
                return self._source_to_ascii(value, source_base)

            # Conversion base->base numérique (incl. 64 via bytes)
            decimal_value = self._to_decimal(value, source_base)
            return self._from_decimal(decimal_value, target_base)
        except Exception as exc:
            return f"Erreur: {exc}"

    def _ascii_to_target(self, value: str, target_base: str) -> str:
        if target_base in {"2", "8", "10", "16", "36"}:
            parts: List[str] = []
            for ch in value:
                code = ord(ch)
                if target_base == "2":
                    parts.append(bin(code)[2:])
                elif target_base == "8":
                    parts.append(oct(code)[2:])
                elif target_base == "10":
                    parts.append(str(code))
                elif target_base == "16":
                    parts.append(hex(code)[2:])
                elif target_base == "36":
                    parts.append(self._to_base36(code))
            return " ".join(parts)

        if target_base == "64":
            return base64.b64encode(value.encode("utf-8")).decode("ascii")

        raise ValueError(f"Conversion ASCII -> base {target_base} non supportée")

    def _source_to_ascii(self, value: str, source_base: str) -> str:
        if source_base in {"2", "8", "10", "36"}:
            chunks = value.split()
            out: List[str] = []
            for chunk in chunks:
                if not chunk.strip():
                    continue
                out.append(chr(int(chunk, int(source_base))))
            return "".join(out)

        if source_base == "16":
            clean = re.sub(r"[^0-9A-Fa-f]", "", value)
            if len(clean) % 2 != 0:
                clean = clean + "0"
            return bytes.fromhex(clean).decode("utf-8", errors="replace")

        if source_base == "64":
            raw = base64.b64decode(value)
            return raw.decode("utf-8", errors="replace")

        raise ValueError(f"Conversion base {source_base} -> ASCII non supportée")

    def _to_decimal(self, value: str, source_base: str) -> int:
        if source_base == "10":
            return int(value)
        if source_base in {"2", "8", "16", "36"}:
            clean = value
            if source_base == "16":
                clean = value.replace("0x", "").replace("0X", "")
            return int(clean, int(source_base))
        if source_base == "64":
            raw = base64.b64decode(value)
            return int.from_bytes(raw, byteorder="big", signed=False)
        raise ValueError(f"Base source non supportée: {source_base}")

    def _from_decimal(self, value: int, target_base: str) -> str:
        if target_base == "2":
            return bin(value)[2:]
        if target_base == "8":
            return oct(value)[2:]
        if target_base == "10":
            return str(value)
        if target_base == "16":
            return hex(value)[2:]
        if target_base == "36":
            return self._to_base36(value)
        if target_base == "64":
            if value == 0:
                return base64.b64encode(b"\x00").decode("ascii")
            byte_length = (value.bit_length() + 7) // 8
            raw = value.to_bytes(byte_length, byteorder="big")
            return base64.b64encode(raw).decode("ascii")
        raise ValueError(f"Base cible non supportée: {target_base}")

    def _to_base36(self, value: int) -> str:
        chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        if value == 0:
            return "0"
        out = ""
        v = value
        while v > 0:
            out = chars[v % 36] + out
            v //= 36
        return out

    def _decode_fragments(self, text: str, fragments: List[Dict[str, Any]], source_base: str, target_base: str) -> str:
        # Trier par start décroissant pour préserver les indices.
        sorted_fragments = sorted(fragments, key=lambda x: x["start"], reverse=True)
        result_text = text

        for fragment in sorted_fragments:
            original = fragment["value"]
            converted = self._convert_base(original, source_base=source_base, target_base=target_base)
            start, end = fragment["start"], fragment["end"]
            result_text = result_text[:start] + converted + result_text[end:]

        return result_text

    def _bruteforce_convert(self, input_value: str, strict_mode: bool, embedded: bool, enable_gps_detection: bool) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        result_id = 1

        for source_base in self._supported_bases:
            check = self._check_code(input_value, base=source_base, strict=strict_mode, embedded=embedded)
            if not check["is_match"]:
                continue

            for target_base in self._supported_bases:
                if target_base == source_base:
                    continue

                try:
                    if embedded:
                        converted = self._decode_fragments(input_value, fragments=check["fragments"], source_base=source_base, target_base=target_base)
                    else:
                        converted = self._convert_base(input_value, source_base=source_base, target_base=target_base)

                    if converted.startswith("Erreur:"):
                        continue

                    confidence = self._evaluate_result_quality(converted, enable_gps_detection=enable_gps_detection)
                    if confidence < 0.1:
                        continue

                    results.append(
                        {
                            "id": f"result_{result_id}",
                            "text_output": converted,
                            "confidence": float(confidence),
                            "parameters": {
                                "source_base": source_base,
                                "target_base": target_base,
                                "fragments_count": len(check["fragments"]) if embedded else 1,
                            },
                            "metadata": {
                                "fragments": [f["value"] for f in check["fragments"]] if embedded else [input_value]
                            },
                        }
                    )
                    result_id += 1
                except (binascii.Error, ValueError, UnicodeDecodeError):
                    continue

        results.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
        return results

    def _evaluate_result_quality(self, converted_value: str, enable_gps_detection: bool) -> float:
        if converted_value.startswith("Erreur:"):
            return 0.0

        total = len(converted_value)
        if total == 0:
            return 0.0

        printable = sum(1 for c in converted_value if 32 <= ord(c) <= 126)
        printable_ratio = printable / total

        word_matches = re.findall(r"[a-zA-Z]{3,}", converted_value)
        words = len(word_matches)

        gps_confidence = 0.0
        if enable_gps_detection:
            gps_patterns = [
                r"[NS]\s*\d{1,2}\s*[° ]\s*\d{1,2}\.\d+",
                r"[EW]\s*\d{1,3}\s*[° ]\s*\d{1,2}\.\d+",
            ]
            if any(re.search(p, converted_value) for p in gps_patterns):
                gps_confidence = 0.8

        if gps_confidence > 0:
            return gps_confidence

        score = printable_ratio * 0.6
        score += min(0.4, words * 0.05)
        if words == 0:
            score = min(score, 0.2)
        return min(1.0, score)

    def _get_plugin_info(self, start_time: float) -> Dict[str, Any]:
        execution_time = (time.time() - start_time) * 1000
        return {"name": self.name, "version": self.version, "execution_time_ms": round(execution_time, 2)}

    def _error_response(self, message: str, start_time: float) -> Dict[str, Any]:
        return {"status": "error", "summary": message, "results": [], "plugin_info": self._get_plugin_info(start_time)}
