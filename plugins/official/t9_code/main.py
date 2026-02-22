from __future__ import annotations

import re
import time
from itertools import product
from typing import Any, Dict, List

try:
    from gc_backend.plugins.scoring import score_text, score_text_fast

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text = None
    score_text_fast = None
    _SCORING_AVAILABLE = False


class T9CodePlugin:
    """T9 encode/decode with safe bruteforce and optional scoring."""

    def __init__(self) -> None:
        self.name = "t9_code"
        self.version = "1.0.1"

        # Safety limits
        self.MAX_SEQUENCE_LENGTH = 12
        self.MAX_COMBINATIONS_PER_SEGMENT = 1000
        self.MAX_FINAL_CANDIDATES = 50
        self.MAX_SEGMENTS = 10
        self.MAX_EXECUTION_TIME = 10  # seconds

        self.t9_mapping: Dict[str, str] = {
            "2": "ABC",
            "3": "DEF",
            "4": "GHI",
            "5": "JKL",
            "6": "MNO",
            "7": "PQRS",
            "8": "TUV",
            "9": "WXYZ",
        }
        self.letter_to_t9: Dict[str, str] = {
            letter: digit for digit, letters in self.t9_mapping.items() for letter in letters
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    def _encode_text(self, text: str) -> str:
        if len(text) > 50:
            text = text[:50]
        result: List[str] = []
        for ch in text.upper():
            if ch in self.letter_to_t9:
                result.append(self.letter_to_t9[ch])
            elif ch == " ":
                result.append("0")
        return "".join(result)

    def _generate_combinations_safe(self, sequence: str) -> List[str]:
        if not sequence or len(sequence) > self.MAX_SEQUENCE_LENGTH:
            return []
        combinations: List[str] = [""]
        for digit in sequence:
            if digit not in self.t9_mapping:
                return []
            letters = self.t9_mapping[digit]
            new_combinations: List[str] = []
            for combo in combinations:
                for letter in letters:
                    new_combinations.append(combo + letter)
                    if len(new_combinations) >= self.MAX_COMBINATIONS_PER_SEGMENT:
                        break
                if len(new_combinations) >= self.MAX_COMBINATIONS_PER_SEGMENT:
                    break
            combinations = new_combinations
            if len(combinations) >= self.MAX_COMBINATIONS_PER_SEGMENT:
                break
        return combinations[: self.MAX_COMBINATIONS_PER_SEGMENT]

    def _filter_segment_words(self, combinations: List[str], language: str, original_sequence: str) -> List[Dict[str, Any]]:
        if not combinations:
            return []
        priority_words = {
            "DCODE": 0.95,
            "CODE": 0.90,
            "CACHE": 0.85,
            "MONDE": 0.90,
            "HELLO": 0.80,
            "THE": 0.75,
            "AREA": 0.80,
            "BONJOUR": 0.85,
            "AMI": 0.85,
            "CHER": 0.80,
            "BON": 0.80,
            "OUI": 0.85,
            "NON": 0.85,
            "MER": 0.80,
            "TER": 0.80,
            "FIN": 0.80,
            "DEBUT": 0.85,
            "MILIEU": 0.80,
            "CENTER": 0.80,
            "START": 0.80,
            "END": 0.80,
            "GO": 0.85,
            "STOP": 0.80,
            "YES": 0.85,
            "NO": 0.85,
            "OK": 0.90,
            "HI": 0.85,
            "BYE": 0.80,
        }

        valid_words: List[Dict[str, Any]] = []
        for candidate in combinations:
            if len(candidate) < 2:
                continue
            candidate_upper = candidate.upper()
            if candidate_upper in priority_words:
                valid_words.append(
                    {
                        "word": candidate,
                        "score": priority_words[candidate_upper],
                        "language": "priority",
                        "length": len(candidate),
                        "original_sequence": original_sequence,
                    }
                )
                continue
            # Without dictionary service, fallback to a low score baseline
            valid_words.append(
                {
                    "word": candidate,
                    "score": 0.3,
                    "language": language,
                    "length": len(candidate),
                    "original_sequence": original_sequence,
                }
            )

        valid_words.sort(key=lambda x: x["score"], reverse=True)
        return valid_words[:20]

    def _generate_phrase_candidates(self, all_word_candidates: List[List[Dict[str, Any]]], max_results: int) -> List[Dict[str, Any]]:
        if not all_word_candidates:
            return []
        if len(all_word_candidates) == 1:
            return [
                {
                    "phrase": word["word"],
                    "words": [word],
                    "total_score": word["score"],
                    "avg_score": word["score"],
                }
                for word in all_word_candidates[0][:max_results]
            ]

        phrase_candidates: List[Dict[str, Any]] = []
        limited_candidates = [segment[: min(5, len(segment))] for segment in all_word_candidates]

        for combination in product(*limited_candidates):
            phrase = " ".join(word["word"] for word in combination)
            total_score = sum(word["score"] for word in combination)
            avg_score = total_score / len(combination)
            phrase_candidates.append(
                {
                    "phrase": phrase,
                    "words": list(combination),
                    "total_score": total_score,
                    "avg_score": avg_score,
                }
            )
            if len(phrase_candidates) >= max_results * 2:
                break

        return phrase_candidates

    def _finalize_scoring(self, phrase_candidates: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
        if not phrase_candidates:
            return []
        final_results: List[Dict[str, Any]] = []
        for candidate in phrase_candidates:
            length_bonus = 0.1 if len(candidate["phrase"]) <= 10 else 0.05 if len(candidate["phrase"]) <= 20 else 0
            short_word_bonus = sum(0.05 for word in candidate["words"] if len(word["word"]) <= 3)
            final_score = min(candidate["avg_score"] + length_bonus + short_word_bonus, 1.0)
            final_results.append(
                {
                    "word": candidate["phrase"],
                    "score": final_score,
                    "language": "mixed",
                    "length": len(candidate["phrase"]),
                    "metadata": {
                        "word_count": len(candidate["words"]),
                        "avg_word_score": candidate["avg_score"],
                        "length_bonus": length_bonus,
                        "short_word_bonus": short_word_bonus,
                    },
                }
            )

        final_results.sort(key=lambda x: x["score"], reverse=True)
        return final_results[:max_results]

    def decode_safe(self, text: str, language: str = "auto", max_results: int = 10) -> List[Dict[str, Any]]:
        start_time = time.time()
        text = re.sub(r"[^0-9]", "", text)
        if not text or len(text) > 50:
            return []

        normalized_text = re.sub(r"0+", "0", text).strip("0")
        segments = [seg for seg in normalized_text.split("0") if seg]
        if not segments or len(segments) > self.MAX_SEGMENTS:
            return []

        all_word_candidates: List[List[Dict[str, Any]]] = []
        for segment in segments:
            if time.time() - start_time > self.MAX_EXECUTION_TIME:
                break
            if len(segment) > 10:
                continue
            combinations = self._generate_combinations_safe(segment)
            if not combinations:
                continue
            segment_words = self._filter_segment_words(combinations, language, segment)
            all_word_candidates.append(segment_words)

        if not all_word_candidates:
            return []

        phrase_candidates = self._generate_phrase_candidates(all_word_candidates, max_results)
        return self._finalize_scoring(phrase_candidates, max_results)

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
        text = str(inputs.get("text", ""))
        language = str(inputs.get("language", "auto"))
        max_results = min(int(inputs.get("max_results", 10)), self.MAX_FINAL_CANDIDATES)
        context = inputs.get("context", {})
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))
        bruteforce_flag = self._is_truthy(inputs.get("bruteforce", False)) or mode == "bruteforce"

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

        if not text:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = "Aucun texte fourni"
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        try:
            if mode == "encode":
                encoded = self._encode_text(text)
                result_entry = {
                    "id": "result_1",
                    "text_output": encoded,
                    "confidence": 1.0,
                    "parameters": {"mode": "encode"},
                    "metadata": {"original_text": text},
                }
                scoring_info = self._get_score(encoded, context) if enable_scoring else None
                if scoring_info:
                    result_entry["scoring"] = scoring_info
                standardized_response["results"].append(result_entry)
                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Encodage T9 réussi",
                    }
                )

            elif mode in {"decode", "bruteforce"} or bruteforce_flag:
                decoded_results = self.decode_safe(text, language, max_results)
                for i, result in enumerate(decoded_results):
                    confidence = min(result.get("score", 0.0), 1.0)
                    if enable_scoring:
                        fast_score = self._get_score_fast(result["word"])
                        if fast_score > 0:
                            confidence = fast_score
                    entry = {
                        "id": f"result_{i + 1}",
                        "text_output": result["word"],
                        "confidence": confidence,
                        "parameters": {
                            "mode": "decode",
                            "language": language,
                            "bruteforce": True,
                        },
                        "metadata": {
                            "detected_language": result.get("language", "unknown"),
                            "original_score": result.get("score"),
                            "word_length": result.get("length"),
                            "word_count": result.get("metadata", {}).get("word_count", 1),
                        },
                    }
                    standardized_response["results"].append(entry)

                # Fallback: if no decoded results, provide a naive first-letter mapping suggestion
                if not standardized_response["results"]:
                    first_letter_map = {digit: letters[0] for digit, letters in self.t9_mapping.items()}
                    segments = [seg for seg in re.sub(r"0+", "0", re.sub(r"[^0-9]", "", text)).strip("0").split("0") if seg]
                    naive_phrase = " ".join("".join(first_letter_map.get(d, "") for d in seg) for seg in segments)
                    standardized_response["results"].append(
                        {
                            "id": "result_1",
                            "text_output": naive_phrase,
                            "confidence": 0.1,
                            "parameters": {
                                "mode": "decode",
                                "language": language,
                                "bruteforce": True,
                                "fallback": True,
                            },
                            "metadata": {
                                "fallback": True,
                                "segments": segments,
                            },
                        }
                    )

                standardized_response["results"].sort(key=lambda r: r.get("confidence", 0.0), reverse=True)
                standardized_response["summary"].update(
                    {
                        "best_result_id": standardized_response["results"][0]["id"] if standardized_response["results"] else None,
                        "total_results": len(standardized_response["results"]),
                        "message": f"{len(standardized_response['results'])} solution(s) T9",
                    }
                )

            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode inconnu : {mode}"

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur: {exc}"

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return T9CodePlugin().execute(inputs)
