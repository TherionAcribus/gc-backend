from __future__ import annotations

import re
import time
from typing import Any, Dict, List


class RailFenceCipherPlugin:
    def __init__(self) -> None:
        self.name = "rail_fence_cipher"
        self.version = "1.0.0"

        try:
            from gc_backend.plugins.scoring import score_text, score_text_fast

            self._score_text = score_text
            self._score_text_fast = score_text_fast
            self._scoring_available = True
        except Exception:
            self._score_text = None
            self._score_text_fast = None
            self._scoring_available = False

    @staticmethod
    def _rail_pattern(length: int, key: int, from_top: bool = True) -> List[int]:
        if key <= 1:
            return [0] * length
        pattern: List[int] = []
        row = 0 if from_top else key - 1
        direction = 1 if from_top else -1
        for _ in range(length):
            pattern.append(row)
            if row == 0:
                direction = 1
            elif row == key - 1:
                direction = -1
            row += direction
        return pattern

    @classmethod
    def _encode_rail_fence(cls, text: str, key: int, from_top: bool = True) -> str:
        if key <= 1:
            return text
        pattern = cls._rail_pattern(len(text), key, from_top)
        rails = ["" for _ in range(key)]
        for ch, row in zip(text, pattern):
            rails[row] += ch
        return "".join(rails)

    @classmethod
    def _decode_rail_fence(cls, cipher: str, key: int, from_top: bool = True) -> str:
        if key <= 1:
            return cipher
        length = len(cipher)
        pattern = cls._rail_pattern(length, key, from_top)
        rail_counts = [pattern.count(r) for r in range(key)]
        rails: List[str] = []
        idx = 0
        for count in rail_counts:
            rails.append(cipher[idx : idx + count])
            idx += count
        rail_positions = [0] * key
        plaintext_chars: List[str] = []
        for row in pattern:
            plaintext_chars.append(rails[row][rail_positions[row]])
            rail_positions[row] += 1
        return "".join(plaintext_chars)

    def _clean_text_for_scoring(self, text: str) -> str:
        cleaned = re.sub(r"[^\w\s]", "", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def _get_text_score(self, text: str, context: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
        if not self._scoring_available or not self._score_text:
            return None
        cleaned_text = self._clean_text_for_scoring(text)
        try:
            return self._score_text(cleaned_text, context=context or {})
        except Exception:
            return None

    def _get_text_score_fast(self, text: str) -> float:
        if not self._scoring_available or not self._score_text_fast:
            return 0.3
        try:
            return self._score_text_fast(self._clean_text_for_scoring(text))
        except Exception:
            return 0.3

    def _confidence_for_key(self, key: int) -> float:
        if key <= 3:
            return 0.8
        if key <= 6:
            return 0.6
        return 0.4

    def _is_truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).lower() in {"true", "on", "1", "yes"}

    def encode(self, text: str, key: int, from_top: bool) -> str:
        return self._encode_rail_fence(text, key, from_top)

    def decode(self, text: str, key: int, from_top: bool) -> str:
        return self._decode_rail_fence(text, key, from_top)

    def bruteforce(self, text: str, max_key: int, from_top: bool) -> List[Dict[str, Any]]:
        max_key = max(2, min(max_key, max(2, len(text))))
        solutions: List[Dict[str, Any]] = []
        for k in range(2, max_key + 1):
            decoded = self.decode(text, k, from_top)
            solutions.append({"key": k, "decoded_text": decoded})
        return solutions

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        key = int(inputs.get("key", 3) or 3)
        start_direction = str(inputs.get("start_direction", "top")).lower()
        from_top = start_direction != "bottom"
        max_key = int(inputs.get("max_key", 10) or 10)
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))
        context = inputs.get("context", {})

        bruteforce_flag = self._is_truthy(inputs.get("bruteforce", False)) or self._is_truthy(
            inputs.get("brute_force", False)
        )
        do_bruteforce = mode == "bruteforce" or bruteforce_flag

        standardized_response = {
            "status": "success",
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time": 0,
            },
            "inputs": inputs.copy(),
            "results": [],
            "summary": {
                "best_result_id": None,
                "total_results": 0,
                "message": "",
            },
        }

        if not isinstance(text, str) or text == "":
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = "Aucun texte fourni"
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        try:
            if mode == "encode":
                cipher_text = self.encode(text, key, from_top)
                result_obj = {
                    "id": "result_1",
                    "text_output": cipher_text,
                    "confidence": 1.0,
                    "parameters": {
                        "mode": mode,
                        "key": key,
                        "start_direction": start_direction,
                    },
                    "metadata": {"processed_chars": len(text)},
                }
                standardized_response["results"].append(result_obj)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Encodage réussi",
                    }
                )

            elif mode == "decode" and not do_bruteforce:
                decoded_text = self.decode(text, key, from_top)
                confidence = self._confidence_for_key(key)
                scoring_info = None
                if enable_scoring:
                    scoring_info = self._get_text_score(decoded_text, context)
                    if scoring_info and "score" in scoring_info:
                        confidence = float(scoring_info["score"])

                result_obj = {
                    "id": "result_1",
                    "text_output": decoded_text,
                    "confidence": confidence,
                    "parameters": {
                        "mode": mode,
                        "key": key,
                        "start_direction": start_direction,
                    },
                    "metadata": {"processed_chars": len(text)},
                }
                if scoring_info:
                    result_obj["scoring"] = scoring_info

                standardized_response["results"].append(result_obj)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Décodage réussi",
                    }
                )

            elif do_bruteforce:
                solutions = self.bruteforce(text, max_key, from_top)
                results: List[Dict[str, Any]] = []
                for idx, sol in enumerate(solutions, start=1):
                    confidence = self._confidence_for_key(sol["key"])
                    if enable_scoring:
                        fast_score = self._get_text_score_fast(sol["decoded_text"])
                        if fast_score > 0:
                            confidence = fast_score

                    res = {
                        "id": f"result_{idx}",
                        "text_output": sol["decoded_text"],
                        "confidence": confidence,
                        "parameters": {
                            "mode": "decode",
                            "key": sol["key"],
                            "start_direction": start_direction,
                            "bruteforce": True,
                        },
                        "metadata": {"processed_chars": len(text)},
                    }
                    results.append(res)

                results.sort(key=lambda r: r.get("confidence", 0.0), reverse=True)
                standardized_response["results"] = results
                standardized_response["summary"].update(
                    {
                        "best_result_id": results[0]["id"] if results else None,
                        "total_results": len(results),
                        "message": f"{len(results)} clés testées",
                    }
                )

            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode non reconnu : {mode}"

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur lors du traitement: {exc}"

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return RailFenceCipherPlugin().execute(inputs)
