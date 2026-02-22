from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Tuple


class PrimeNumbersPlugin:
    def __init__(self) -> None:
        self.name = "prime_numbers"
        self.version = "1.0.0"

        self.encode_table: Dict[str, str] = {
            "A": "2",
            "B": "3",
            "C": "5",
            "D": "7",
            "E": "11",
            "F": "13",
            "G": "17",
            "H": "19",
            "I": "23",
            "J": "29",
            "K": "31",
            "L": "37",
            "M": "41",
            "N": "43",
            "O": "47",
            "P": "53",
            "Q": "59",
            "R": "61",
            "S": "67",
            "T": "71",
            "U": "73",
            "V": "79",
            "W": "83",
            "X": "89",
            "Y": "97",
            "Z": "101",
        }
        self.decode_table: Dict[str, str] = {v: k for k, v in self.encode_table.items()}
        self.default_separators = " ,;\t\r\n"
        self._prime_values = sorted(self.decode_table.keys(), key=len, reverse=True)

        try:
            from gc_backend.plugins.scoring import score_text, score_text_fast

            self._score_text = score_text
            self._score_text_fast = score_text_fast
            self._scoring_available = True
        except Exception:
            self._score_text = None
            self._score_text_fast = None
            self._scoring_available = False

    def _get_plugin_info(self, start_time: float) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "execution_time": int((time.time() - start_time) * 1000),
        }

    def _error_response(self, message: str, start_time: float) -> Dict[str, Any]:
        return {
            "status": "error",
            "summary": {"message": message, "best_result_id": None, "total_results": 0},
            "results": [],
            "plugin_info": self._get_plugin_info(start_time),
        }

    def _get_text_score(self, text: str, context: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
        if not self._scoring_available or not self._score_text:
            return None
        try:
            return self._score_text(text, context=context or {})
        except Exception:
            return None

    def _get_text_score_fast(self, text: str) -> float:
        if not self._scoring_available or not self._score_text_fast:
            return 0.0
        try:
            return self._score_text_fast(text)
        except Exception:
            return 0.0

    def _separator_to_string(self, separator: str, custom: str) -> str:
        if separator == "comma":
            return ","
        if separator == "space":
            return " "
        if separator == "semicolon":
            return ";"
        if separator == "newline":
            return "\n"
        if separator == "tab":
            return "\t"
        if separator == "none":
            return ""
        if separator == "custom":
            return custom
        return " "

    def _split_tokens(self, text: str, separator: str, custom: str) -> List[str]:
        if not text:
            return []
        if separator == "comma":
            tokens = text.split(",")
        elif separator == "space":
            tokens = text.split()
        elif separator == "semicolon":
            tokens = text.split(";")
        elif separator == "newline":
            tokens = text.splitlines()
        elif separator == "tab":
            tokens = text.split("\t")
        elif separator == "custom" and custom:
            tokens = text.split(custom)
        elif separator == "none":
            return [text.strip()]
        else:
            tokens = re.split(r"[\s,;]+", text.strip())
        return [token for token in tokens if token]

    def _segment_concatenated(self, text: str, max_results: int = 25) -> List[List[str]]:
        if not text:
            return []
        text = re.sub(r"[^0-9]", "", text)
        if not text:
            return []

        results: List[List[str]] = []

        def dfs(index: int, path: List[str]) -> None:
            if len(results) >= max_results:
                return
            if index == len(text):
                results.append(path.copy())
                return
            for prime in self._prime_values:
                if text.startswith(prime, index):
                    path.append(prime)
                    dfs(index + len(prime), path)
                    path.pop()

        dfs(0, [])
        return results

    def _decode_tokens(self, tokens: List[str]) -> Tuple[str, int]:
        output: List[str] = []
        hits = 0
        for token in tokens:
            if token in self.decode_table:
                output.append(self.decode_table[token])
                hits += 1
            else:
                output.append("?")
        return "".join(output), hits

    def encode(self, text: str, separator: str, custom_separator: str) -> str:
        output: List[str] = []
        for ch in text.upper():
            if ch in self.encode_table:
                output.append(self.encode_table[ch])
            else:
                output.append(ch)
        sep = self._separator_to_string(separator, custom_separator)
        return sep.join(output)

    def decode(self, text: str, separator: str, custom_separator: str, enable_scoring: bool, context: Dict[str, Any]) -> str:
        tokens = self._split_tokens(text, separator, custom_separator)
        if separator == "none":
            segmentations = self._segment_concatenated(text)
            if not segmentations:
                decoded, _ = self._decode_tokens(tokens)
                return decoded

            best_output = ""
            best_score = -1.0
            for segmentation in segmentations:
                decoded, hits = self._decode_tokens(segmentation)
                score = hits / len(segmentation) if segmentation else 0.0
                if enable_scoring:
                    scoring = self._get_text_score(decoded, context)
                    if scoring and "score" in scoring:
                        score = float(scoring["score"])
                if score > best_score:
                    best_score = score
                    best_output = decoded
            return best_output

        decoded, _ = self._decode_tokens(tokens)
        return decoded

    def detect(self, text: str, separator: str, custom_separator: str) -> Tuple[bool, float]:
        tokens = self._split_tokens(text, separator, custom_separator)
        tokens = [t for t in tokens if t]
        if not tokens:
            return False, 0.0

        hits = sum(1 for token in tokens if token in self.decode_table)
        score = hits / len(tokens)
        return score >= 0.25, float(score)

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        separator = str(inputs.get("separator", "auto")).lower()
        separator_custom = str(inputs.get("separator_custom", ""))
        enable_scoring = bool(inputs.get("enable_scoring", True))
        is_bruteforce = bool(inputs.get("bruteforce", False) or inputs.get("brute_force", False))
        context = inputs.get("context", {})

        if not isinstance(text, str) or not text:
            return self._error_response("Aucun texte fourni", start_time)

        try:
            if mode == "encode":
                output = self.encode(text, separator, separator_custom)
                result = {
                    "id": "result_1",
                    "text_output": output,
                    "confidence": 1.0,
                    "parameters": {
                        "mode": "encode",
                        "separator": separator,
                        "separator_custom": separator_custom,
                    },
                    "metadata": {"processed_chars": len(text)},
                }

                return {
                    "status": "success",
                    "plugin_info": self._get_plugin_info(start_time),
                    "inputs": inputs,
                    "results": [result],
                    "summary": {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Encodage nombres premiers réussi",
                    },
                }

            if mode == "decode":
                if is_bruteforce:
                    separators = ["auto", "comma", "space", "semicolon", "newline", "tab", "none"]
                    if separator_custom:
                        separators.append("custom")

                    results: List[Dict[str, Any]] = []
                    for sep in separators:
                        tokens = self._split_tokens(text, sep, separator_custom)
                        if sep == "none":
                            segmentations = self._segment_concatenated(text)
                            if not segmentations:
                                segmentations = [tokens]
                        else:
                            segmentations = [tokens]

                        for idx, segmentation in enumerate(segmentations, 1):
                            decoded, hits = self._decode_tokens(segmentation)
                            confidence = hits / len(segmentation) if segmentation else 0.0
                            if enable_scoring:
                                fast_score = self._get_text_score_fast(decoded)
                                if fast_score > 0:
                                    confidence = fast_score

                            result = {
                                "id": f"result_{len(results) + 1}",
                                "text_output": decoded,
                                "confidence": confidence,
                                "parameters": {
                                    "mode": "decode",
                                    "separator": sep,
                                    "separator_custom": separator_custom,
                                    "bruteforce": True,
                                },
                                "metadata": {
                                    "processed_chars": len(text),
                                    "tokens": segmentation,
                                    "segmentation_index": idx,
                                    "token_hits": hits,
                                },
                            }
                            results.append(result)

                    results.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
                    if not results:
                        return self._error_response("Aucun résultat bruteforce", start_time)

                    return {
                        "status": "success",
                        "plugin_info": self._get_plugin_info(start_time),
                        "inputs": inputs,
                        "results": results,
                        "summary": {
                            "best_result_id": results[0]["id"],
                            "total_results": len(results),
                            "message": f"Bruteforce: {len(results)} variantes testées",
                        },
                    }

                output = self.decode(text, separator, separator_custom, enable_scoring, context)
                confidence = 0.7
                scoring_result = None
                if enable_scoring:
                    scoring_result = self._get_text_score(output, context)
                    if scoring_result and "score" in scoring_result:
                        confidence = float(scoring_result["score"])

                result = {
                    "id": "result_1",
                    "text_output": output,
                    "confidence": confidence,
                    "parameters": {
                        "mode": "decode",
                        "separator": separator,
                        "separator_custom": separator_custom,
                    },
                    "metadata": {"processed_chars": len(text)},
                }
                if scoring_result:
                    result["scoring"] = scoring_result

                return {
                    "status": "success",
                    "plugin_info": self._get_plugin_info(start_time),
                    "inputs": inputs,
                    "results": [result],
                    "summary": {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Décodage nombres premiers réussi",
                    },
                }

            if mode == "detect":
                is_match, score = self.detect(text, separator, separator_custom)
                result = {
                    "id": "result_1",
                    "text_output": f"Probabilité Prime Numbers: {score:.2%}",
                    "confidence": float(score),
                    "parameters": {
                        "mode": "detect",
                        "separator": separator,
                        "separator_custom": separator_custom,
                    },
                    "metadata": {"is_match": is_match, "detection_score": float(score)},
                }

                return {
                    "status": "success",
                    "plugin_info": self._get_plugin_info(start_time),
                    "inputs": inputs,
                    "results": [result],
                    "summary": {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Code Prime Numbers détecté" if is_match else "Aucun code Prime Numbers détecté",
                    },
                }

            return self._error_response(f"Mode inconnu: {mode}", start_time)

        except Exception as exc:
            return self._error_response(f"Erreur lors du traitement: {exc}", start_time)


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return PrimeNumbersPlugin().execute(inputs)
