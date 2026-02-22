from __future__ import annotations

import re
import string
import time
from typing import Any, Dict, List

try:
    from gc_backend.plugins.scoring import score_text, score_text_fast

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text = None
    score_text_fast = None
    _SCORING_AVAILABLE = False


class VigenereCipherPlugin:
    """Encode/decode using Vigenère cipher."""

    def __init__(self) -> None:
        self.name = "vigenere_cipher"
        self.version = "1.0.0"

    # ------------------------------------------------------------------
    # Core Vigenere logic
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_key(key: str) -> str:
        return "".join([c for c in key.upper() if c in string.ascii_uppercase])

    def _full_key(self, key: str, length: int) -> str:
        cleaned = self._clean_key(key)
        if not cleaned:
            raise ValueError("La clé doit contenir au moins une lettre A-Z")
        return (cleaned * (length // len(cleaned) + 1))[:length]

    def encode(self, text: str, key: str) -> str:
        full_key = self._full_key(key, len(text))
        result = []
        key_index = 0
        for ch in text:
            if ch.upper() in string.ascii_uppercase:
                shift = ord(full_key[key_index]) - ord("A")
                encoded_char = chr((ord(ch.upper()) - ord("A") + shift) % 26 + ord("A"))
                if ch.islower():
                    encoded_char = encoded_char.lower()
                result.append(encoded_char)
                key_index += 1
            else:
                result.append(ch)
        return "".join(result)

    def decode(self, text: str, key: str) -> str:
        full_key = self._full_key(key, len(text))
        result = []
        key_index = 0
        for ch in text:
            if ch.upper() in string.ascii_uppercase:
                shift = ord(full_key[key_index]) - ord("A")
                decoded_char = chr((ord(ch.upper()) - ord("A") - shift) % 26 + ord("A"))
                if ch.islower():
                    decoded_char = decoded_char.lower()
                result.append(decoded_char)
                key_index += 1
            else:
                result.append(ch)
        return "".join(result)

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    @staticmethod
    def _parse_candidate_keys(keys_input: Any) -> List[str]:
        if not keys_input:
            return []
        if isinstance(keys_input, list):
            raw = keys_input
        else:
            raw = re.split(r"[;,\s]+", str(keys_input))
        return [key.strip() for key in raw if key.strip()]

    def _get_score(self, text: str, context: Dict[str, Any]) -> Dict[str, Any] | None:
        if not _SCORING_AVAILABLE or not score_text:
            return None
        try:
            return score_text(text, context=context)
        except Exception:
            return None

    @staticmethod
    def _get_score_fast(text: str) -> float:
        if not _SCORING_AVAILABLE or not score_text_fast:
            return 0.3
        try:
            return score_text_fast(text)
        except Exception:
            return 0.3

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        key = inputs.get("key", "")
        strict_mode = str(inputs.get("strict", "smooth")).lower() == "strict"
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))
        context = inputs.get("context", {})
        candidate_keys = self._parse_candidate_keys(inputs.get("candidate_keys"))
        max_results = min(int(inputs.get("max_results", 10) or 10), 50)
        do_bruteforce = mode == "bruteforce" or self._is_truthy(inputs.get("bruteforce", False))

        response = {
            "status": "success",
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time": 0,
            },
            "inputs": inputs.copy(),
            "results": [],
            "summary": {"best_result_id": None, "total_results": 0, "message": ""},
        }

        if not isinstance(text, str) or text == "":
            response["status"] = "error"
            response["summary"]["message"] = "Aucun texte fourni"
            response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return response

        try:
            if mode == "encode":
                encoded = self.encode(text, key)
                response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": encoded,
                        "confidence": 1.0,
                        "parameters": {"mode": mode, "key": key},
                        "metadata": {"processed_chars": len(text)},
                    }
                )
                response["summary"].update(
                    {"best_result_id": "result_1", "total_results": 1, "message": "Encodage réussi"}
                )

            elif mode == "decode" and not do_bruteforce:
                decoded = self.decode(text, key)
                confidence = 0.5
                scoring_result = self._get_score(decoded, context) if enable_scoring else None
                if scoring_result and "score" in scoring_result:
                    confidence = float(scoring_result["score"])

                result = {
                    "id": "result_1",
                    "text_output": decoded,
                    "confidence": confidence,
                    "parameters": {"mode": mode, "key": key, "strict": "strict" if strict_mode else "smooth"},
                    "metadata": {"processed_chars": len(text)},
                }
                if scoring_result:
                    result["scoring"] = scoring_result

                response["results"].append(result)
                response["summary"].update(
                    {"best_result_id": "result_1", "total_results": 1, "message": "Décodage réussi"}
                )

            elif do_bruteforce:
                keys = candidate_keys or ([str(key)] if key else [])
                if not keys:
                    response["status"] = "error"
                    response["summary"]["message"] = "Aucune clé candidate fournie"
                    response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                    return response

                for idx, candidate_key in enumerate(keys[:max_results], 1):
                    decoded = self.decode(text, candidate_key)
                    confidence = self._get_score_fast(decoded) if enable_scoring else 0.3
                    result = {
                        "id": f"result_{idx}",
                        "text_output": decoded,
                        "confidence": confidence,
                        "parameters": {"mode": "decode", "key": candidate_key, "bruteforce": True},
                        "metadata": {"processed_chars": len(text)},
                    }
                    response["results"].append(result)

                response["results"].sort(key=lambda r: r.get("confidence", 0.0), reverse=True)
                response["summary"].update(
                    {
                        "best_result_id": response["results"][0]["id"] if response["results"] else None,
                        "total_results": len(response["results"]),
                        "message": f"{len(response['results'])} solution(s) testée(s)",
                    }
                )

            elif mode == "detect":
                keys = candidate_keys or ([str(key)] if key else [])
                if not keys:
                    response["status"] = "error"
                    response["summary"]["message"] = "Aucune clé candidate fournie"
                    response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                    return response

                best_result = None
                for candidate_key in keys[:max_results]:
                    decoded = self.decode(text, candidate_key)
                    confidence = 0.4
                    scoring_result = self._get_score(decoded, context) if enable_scoring else None
                    if scoring_result and "score" in scoring_result:
                        confidence = float(scoring_result["score"])
                    result = {
                        "id": "result_1",
                        "text_output": decoded,
                        "confidence": confidence,
                        "parameters": {"mode": "detect", "key": candidate_key},
                        "metadata": {"processed_chars": len(text)},
                    }
                    if scoring_result:
                        result["scoring"] = scoring_result
                    if best_result is None or result["confidence"] > best_result["confidence"]:
                        best_result = result

                if best_result is None:
                    response["status"] = "error"
                    response["summary"]["message"] = "Aucun résultat détecté"
                else:
                    response["results"].append(best_result)
                    response["summary"].update(
                        {
                            "best_result_id": "result_1",
                            "total_results": 1,
                            "message": "Détection Vigenère effectuée",
                        }
                    )

            else:
                response["status"] = "error"
                response["summary"]["message"] = f"Mode '{mode}' non pris en charge."

        except Exception as exc:
            response["status"] = "error"
            response["summary"]["message"] = str(exc)

        response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return VigenereCipherPlugin().execute(inputs)
