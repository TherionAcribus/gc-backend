from __future__ import annotations

import re
import string
import time
from typing import Any, Dict, List, Optional, Tuple


class MorseCodePlugin:
    def __init__(self) -> None:
        self.name = "morse_code"
        self.version = "1.2.0"

        self._letter_to_morse: Dict[str, str] = {
            "A": ".-",
            "B": "-...",
            "C": "-.-.",
            "D": "-..",
            "E": ".",
            "F": "..-.",
            "G": "--.",
            "H": "....",
            "I": "..",
            "J": ".---",
            "K": "-.-",
            "L": ".-..",
            "M": "--",
            "N": "-.",
            "O": "---",
            "P": ".--.",
            "Q": "--.-",
            "R": ".-.",
            "S": "...",
            "T": "-",
            "U": "..-",
            "V": "...-",
            "W": ".--",
            "X": "-..-",
            "Y": "-.--",
            "Z": "--..",
            "0": "-----",
            "1": ".----",
            "2": "..---",
            "3": "...--",
            "4": "....-",
            "5": ".....",
            "6": "-....",
            "7": "--...",
            "8": "---..",
            "9": "----.",
        }
        self._morse_to_letter: Dict[str, str] = {v: k for k, v in self._letter_to_morse.items()}

        self._valid_morse_chars = {".", "-"}

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()

        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        embedded = self._parse_bool(inputs.get("embedded", False), default=False)
        allowed_chars = inputs.get("allowed_chars", "")
        if allowed_chars is None:
            allowed_chars = ""
        allowed_chars = str(allowed_chars)

        if not isinstance(text, str) or not text.strip():
            return self._error_response("Aucun texte fourni", start_time)

        if mode == "encode":
            output, processed = self._encode(text)
            return {
                "status": "ok",
                "summary": "Encodage Morse réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": output,
                        "confidence": 1.0,
                        "parameters": {"mode": "encode"},
                        "metadata": {"processed_chars": processed},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "decode":
            if strict_mode and not embedded:
                ok, reason = self._is_text_strictly_morse(text, allowed_chars=allowed_chars)
                if not ok:
                    return self._error_response(f"Code Morse invalide (strict): {reason}", start_time)

            if embedded:
                decoded, fragments = self._decode_embedded(text, strict_mode=strict_mode, allowed_chars=allowed_chars)
                if not fragments:
                    return self._error_response("Aucun code Morse détecté", start_time)

                confidence = self._confidence_from_fragments(text=text, fragments=fragments)
                return {
                    "status": "ok",
                    "summary": "Décodage Morse réussi",
                    "results": [
                        {
                            "id": "result_1",
                            "text_output": decoded,
                            "confidence": float(confidence),
                            "parameters": {
                                "mode": "decode",
                                "strict": "strict" if strict_mode else "smooth",
                                "embedded": True,
                            },
                            "metadata": {"fragments": fragments, "fragments_count": len(fragments)},
                        }
                    ],
                    "plugin_info": self._get_plugin_info(start_time),
                }

            decoded = self._decode_tokens(text)
            if not decoded:
                return self._error_response("Décodage impossible (aucun token Morse valide)", start_time)

            # Confiance neutre (le scoring global peut réévaluer)
            return {
                "status": "ok",
                "summary": "Décodage Morse réussi",
                "results": [
                    {
                        "id": "result_1",
                        "text_output": decoded,
                        "confidence": 0.6 if strict_mode else 0.5,
                        "parameters": {"mode": "decode", "strict": "strict" if strict_mode else "smooth"},
                        "metadata": {"tokens": len(self._split_morse_tokens(text))},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        if mode == "detect":
            is_match, score, fragments = self._detect(text, strict_mode=strict_mode, embedded=embedded, allowed_chars=allowed_chars)
            summary = "Code Morse détecté" if is_match else "Aucun code Morse détecté"
            return {
                "status": "ok",
                "summary": summary,
                "results": [
                    {
                        "id": "result_1",
                        "text_output": f"{summary} (score: {score:.2f})",
                        "confidence": float(score),
                        "parameters": {
                            "mode": "detect",
                            "strict": "strict" if strict_mode else "smooth",
                            "embedded": embedded,
                        },
                        "metadata": {"is_match": is_match, "detection_score": float(score), "fragments": fragments},
                    }
                ],
                "plugin_info": self._get_plugin_info(start_time),
            }

        return self._error_response(f"Mode inconnu: {mode}", start_time)

    def _encode(self, text: str) -> Tuple[str, int]:
        out_tokens: List[str] = []
        processed = 0

        for ch in text:
            up = ch.upper()
            if up in self._letter_to_morse:
                out_tokens.append(self._letter_to_morse[up])
                processed += 1
            elif ch.isspace():
                # Word separator: keep a / token
                # If multiple spaces, we still only add a single separator when needed.
                if out_tokens and out_tokens[-1] != "/":
                    out_tokens.append("/")
            else:
                # Unsupported characters: keep as-is (so the user sees what was skipped)
                out_tokens.append(ch)

        # Clean up duplicate separators
        cleaned: List[str] = []
        for tok in out_tokens:
            if tok == "/" and cleaned and cleaned[-1] == "/":
                continue
            cleaned.append(tok)

        return " ".join(cleaned).strip(), processed

    def _decode_tokens(self, text: str) -> str:
        # Split on whitespace, treat '/' as word separator
        tokens = self._split_morse_tokens(text)
        if not tokens:
            return ""

        out: List[str] = []
        for tok in tokens:
            if tok == "/":
                out.append(" ")
                continue
            out.append(self._morse_to_letter.get(tok, "?"))
        return "".join(out).strip()

    def _split_morse_tokens(self, text: str) -> List[str]:
        # Keep tokens made of dots/dashes or a literal '/'
        raw = re.split(r"\s+", text.strip())
        tokens: List[str] = []
        for tok in raw:
            if not tok:
                continue
            if tok == "/":
                tokens.append(tok)
                continue
            if all(c in self._valid_morse_chars for c in tok):
                tokens.append(tok)
        return tokens

    def _decode_embedded(self, text: str, strict_mode: bool, allowed_chars: str) -> Tuple[str, List[Dict[str, Any]]]:
        # Find sequences containing only . and - and separators
        # Example: ".... . .-.. .-.. ---" or "....../.---" etc.

        if not allowed_chars:
            allowed_chars = " \t\r\n/"

        allowed_set = set(allowed_chars)
        fragments: List[Dict[str, Any]] = []
        chars = list(text)

        # A simple scan to extract contiguous regions composed of morse chars and allowed separators
        start = None
        for i, ch in enumerate(chars + ["\0"]):
            is_allowed = ch in self._valid_morse_chars or ch in allowed_set
            if is_allowed and start is None:
                start = i
            if (not is_allowed) and start is not None:
                end = i
                region = text[start:end]
                if self._region_has_morse_tokens(region):
                    fragments.append({"start": start, "end": end, "value": region})
                start = None

        # Decode fragments in-place (smooth replaces what it can)
        decoded = text
        for frag in sorted(fragments, key=lambda f: f["start"], reverse=True):
            region = frag["value"]
            if strict_mode:
                ok, _reason = self._is_text_strictly_morse(region, allowed_chars=allowed_chars)
                if not ok:
                    continue

            decoded_region = self._decode_tokens(region)
            decoded = decoded[: frag["start"]] + decoded_region + decoded[frag["end"] :]

        return decoded, fragments

    def _region_has_morse_tokens(self, region: str) -> bool:
        return len(self._split_morse_tokens(region)) > 0

    def _is_text_strictly_morse(self, text: str, allowed_chars: str) -> Tuple[bool, str]:
        allowed_set = set(allowed_chars or "")

        has_token = False
        token_buf: List[str] = []

        def flush_token() -> bool:
            nonlocal has_token
            if not token_buf:
                return True
            tok = "".join(token_buf)
            token_buf.clear()
            if tok == "/":
                return True
            if tok in self._morse_to_letter:
                has_token = True
                return True
            return False

        for ch in text:
            if ch in self._valid_morse_chars:
                token_buf.append(ch)
                continue

            # Accept slash as token separator when separated by spaces, or as a char in allowed
            if ch == "/":
                if not flush_token():
                    return False, "token Morse inconnu"
                token_buf.append("/")
                if not flush_token():
                    return False, "token Morse inconnu"
                continue

            if ch.isspace() or ch in allowed_set:
                if not flush_token():
                    return False, "token Morse inconnu"
                continue

            return False, f"caractère non autorisé: {ch!r}"

        if not flush_token():
            return False, "token Morse inconnu"

        if not has_token:
            return False, "aucun token Morse détecté"

        return True, ""

    def _detect(self, text: str, strict_mode: bool, embedded: bool, allowed_chars: str) -> Tuple[bool, float, List[Any]]:
        if embedded:
            _, fragments = self._decode_embedded(text, strict_mode=False, allowed_chars=allowed_chars)
            if not fragments:
                return False, 0.0, []

            # Score: ratio of morse chars in all fragments
            morse_chars = 0
            total_chars = 0
            for frag in fragments:
                seg = str(frag.get("value", ""))
                morse_chars += sum(1 for c in seg if c in self._valid_morse_chars)
                total_chars += len(seg)
            score = morse_chars / total_chars if total_chars else 0.0

            if strict_mode:
                # In strict detect, we require fragments to be strictly valid morse
                strictly_valid = 0
                for frag in fragments:
                    ok, _ = self._is_text_strictly_morse(str(frag.get("value", "")), allowed_chars=allowed_chars)
                    if ok:
                        strictly_valid += 1
                if strictly_valid == 0:
                    return False, 0.0, fragments
                score = min(1.0, score + 0.1)

            return score >= 0.5, float(score), fragments

        ok, _reason = self._is_text_strictly_morse(text, allowed_chars=allowed_chars)
        if ok:
            # Score: fraction of morse chars over total (excluding allowed separators)
            morse_chars = sum(1 for c in text if c in self._valid_morse_chars)
            total_chars = len(text.strip()) or 1
            score = morse_chars / total_chars
            return True, float(min(1.0, score + 0.1)), []

        # Smooth detect: just look for at least one valid token
        tokens = self._split_morse_tokens(text)
        valid_tokens = [t for t in tokens if t in self._morse_to_letter]
        if not valid_tokens:
            return False, 0.0, []

        score = len(valid_tokens) / len(tokens) if tokens else 0.0
        return True, float(score), valid_tokens

    def _confidence_from_fragments(self, text: str, fragments: List[Dict[str, Any]]) -> float:
        if not fragments:
            return 0.0
        total_len = sum(int(f.get("end", 0)) - int(f.get("start", 0)) for f in fragments)
        if total_len <= 0:
            return 0.3
        ratio = min(1.0, total_len / max(1, len(text)))
        return 0.4 + 0.6 * ratio

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
    return MorseCodePlugin().execute(inputs)
