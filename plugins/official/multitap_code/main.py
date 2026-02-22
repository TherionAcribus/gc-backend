from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple


class MultitapCodePlugin:
    def __init__(self) -> None:
        self.name = "multitap_code"
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

        self._encode_table: Dict[str, str] = {
            "A": "2",
            "B": "22",
            "C": "222",
            "D": "3",
            "E": "33",
            "F": "333",
            "G": "4",
            "H": "44",
            "I": "444",
            "J": "5",
            "K": "55",
            "L": "555",
            "M": "6",
            "N": "66",
            "O": "666",
            "P": "7",
            "Q": "77",
            "R": "777",
            "S": "7777",
            "T": "8",
            "U": "88",
            "V": "888",
            "W": "9",
            "X": "99",
            "Y": "999",
            "Z": "9999",
            " ": "0",
        }
        self._decode_table: Dict[str, str] = {v: k for k, v in self._encode_table.items()}

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

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

        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "encode")).lower()
        separator = str(inputs.get("separator", "auto")).lower()
        enable_scoring = bool(inputs.get("enable_scoring", True))
        context = inputs.get("context", {})

        if not isinstance(text, str) or not text:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = "Aucun texte fourni à traiter."
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        bruteforce_param1 = inputs.get("bruteforce", False)
        bruteforce_param2 = inputs.get("brute_force", False)
        do_bruteforce = bool(bruteforce_param1 or bruteforce_param2 or mode == "bruteforce")

        try:
            if do_bruteforce:
                solutions = self._bruteforce(text)
                for idx, solution in enumerate(solutions, 1):
                    separator_type = solution["separator"]
                    decoded_text = solution["decoded_text"]
                    unknown_chars = solution.get("unknown_chars", 0)

                    confidence = self._calculate_confidence(solution, enable_scoring, context)

                    result_entry = {
                        "id": f"result_{idx}",
                        "text_output": decoded_text,
                        "confidence": confidence,
                        "parameters": {
                            "mode": "decode",
                            "separator": separator_type,
                        },
                        "metadata": {
                            "bruteforce_position": idx,
                            "unknown_chars": unknown_chars,
                            "total_chars": len(decoded_text),
                        },
                    }

                    segmentation = solution.get("segmentation")
                    if segmentation:
                        result_entry["metadata"]["segmentation"] = segmentation

                    standardized_response["results"].append(result_entry)

                standardized_response["results"].sort(
                    key=lambda item: item.get("confidence", 0.0), reverse=True
                )

                if standardized_response["results"]:
                    standardized_response["summary"]["best_result_id"] = standardized_response["results"][0]["id"]
                    standardized_response["summary"]["total_results"] = len(standardized_response["results"])
                    standardized_response["summary"][
                        "message"
                    ] = f"Bruteforce Multitap: {len(standardized_response['results'])} variantes testées"
                else:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = "Aucune solution de bruteforce trouvée"

            elif mode == "encode":
                result, processed_chars = self._encode(text, separator)
                standardized_response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": result,
                        "confidence": 1.0,
                        "parameters": {
                            "mode": "encode",
                            "separator": separator,
                        },
                        "metadata": {
                            "processed_chars": processed_chars,
                        },
                    }
                )

                standardized_response["summary"]["best_result_id"] = "result_1"
                standardized_response["summary"]["total_results"] = 1
                standardized_response["summary"][
                    "message"
                ] = f"Encodage Multitap avec séparateur '{separator}' réussi"

            elif mode == "decode":
                result, unknown = self._decode(text, separator)

                if enable_scoring:
                    scoring_result = self._get_text_score(result, context)
                    if scoring_result and "score" in scoring_result:
                        confidence = float(scoring_result["score"])
                    else:
                        confidence = 0.9
                        scoring_result = None
                else:
                    confidence = 0.9
                    scoring_result = None

                result_entry = {
                    "id": "result_1",
                    "text_output": result,
                    "confidence": confidence,
                    "parameters": {
                        "mode": "decode",
                        "separator": separator,
                    },
                    "metadata": {
                        "processed_chars": len(text),
                        "unknown_chars": unknown,
                        "unknown_count": result.count("?"),
                    },
                }

                if scoring_result:
                    result_entry["scoring"] = scoring_result

                standardized_response["results"].append(result_entry)

                standardized_response["summary"]["best_result_id"] = "result_1"
                standardized_response["summary"]["total_results"] = 1
                standardized_response["summary"][
                    "message"
                ] = f"Décodage Multitap avec séparateur '{separator}' réussi"

            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode invalide: {mode}"

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur pendant le traitement: {exc}"

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response

    def _encode(self, text: str, separator: str) -> Tuple[str, int]:
        outputs: List[str] = []
        processed_chars = 0

        for char in text.upper():
            if char in self._encode_table:
                outputs.append(self._encode_table[char])
                processed_chars += 1

        if separator == "dash":
            return "-".join(outputs), processed_chars
        if separator == "none":
            return "".join(outputs), processed_chars
        return " ".join(outputs), processed_chars

    def _decode(self, text: str, separator: str) -> Tuple[str, List[str]]:
        sep = separator
        if sep == "auto":
            sep = self._detect_separator(text)

        if sep == "space":
            groups = [g for g in text.split(" ") if g != ""]
        elif sep == "dash":
            groups = [g for g in text.split("-") if g != ""]
        else:
            groups = self._split_no_separator(text)

        decoded: List[str] = []
        unknown: List[str] = []

        for group in groups:
            value = group.strip()
            if value in self._decode_table:
                decoded.append(self._decode_table[value])
            else:
                decoded.append("?")
                if value:
                    unknown.append(value)

        return "".join(decoded), unknown

    def _split_no_separator(self, text: str) -> List[str]:
        cleaned = re.sub(r"\s+", "", text)
        if not cleaned:
            return []

        segmentations = self._generate_all_segmentations(cleaned)
        if not segmentations:
            return self._split_greedy_fallback(cleaned)

        if len(segmentations) == 1:
            return segmentations[0]

        return self._find_best_segmentation(segmentations)

    def _generate_all_segmentations(self, text: str, max_results: int = 50) -> List[List[str]]:
        def backtrack(pos: int, current: List[str]) -> None:
            if len(results) >= max_results:
                return
            if pos == len(text):
                results.append(current[:])
                return

            for length in range(min(4, len(text) - pos), 0, -1):
                candidate = text[pos : pos + length]
                if candidate in self._decode_table:
                    current.append(candidate)
                    backtrack(pos + length, current)
                    current.pop()

        results: List[List[str]] = []
        backtrack(0, [])

        results.sort(key=self._segmentation_quality_score, reverse=True)
        return results[:max_results]

    def _segmentation_quality_score(self, segmentation: List[str]) -> float:
        decoded_text = "".join(self._decode_table.get(code, "?") for code in segmentation)
        score = 0.0

        fast_score = self._get_text_score_fast(decoded_text)
        score += fast_score * 5.0

        if self._looks_like_french_text(decoded_text):
            score += 3.0

        score += (20 - len(segmentation)) * 0.2

        repetition_penalty = 0
        for i in range(len(decoded_text) - 2):
            if decoded_text[i] == decoded_text[i + 1] == decoded_text[i + 2]:
                repetition_penalty += 1
        score -= repetition_penalty * 2.0

        if 3 <= len(decoded_text) <= 15:
            score += 1.0
        elif len(decoded_text) > 20:
            score -= 1.0

        return score

    def _find_best_segmentation(self, segmentations: List[List[str]]) -> List[str]:
        if not segmentations:
            return []

        best_segmentation = segmentations[0]
        best_score = -1.0

        for segmentation in segmentations:
            decoded_text = "".join(self._decode_table.get(code, "?") for code in segmentation)
            score = 0.0

            fast_score = self._get_text_score_fast(decoded_text)
            score += fast_score * 10.0

            if self._looks_like_french_text(decoded_text):
                score += 50.0

            score += (30 - len(segmentation)) * 2.0

            repetition_penalty = 0
            for i in range(len(decoded_text) - 2):
                if decoded_text[i] == decoded_text[i + 1] == decoded_text[i + 2]:
                    repetition_penalty += 10.0
            score -= repetition_penalty

            if 4 <= len(decoded_text) <= 12:
                score += 10.0
            elif len(decoded_text) <= 3:
                score -= 5.0
            elif len(decoded_text) > 15:
                score -= 10.0

            char_counts: Dict[str, int] = {}
            for char in decoded_text:
                char_counts[char] = char_counts.get(char, 0) + 1
            for count in char_counts.values():
                if len(decoded_text) and count / len(decoded_text) > 0.4:
                    score -= 15.0

            if score > best_score:
                best_score = score
                best_segmentation = segmentation

        return best_segmentation

    def _split_greedy_fallback(self, text: str) -> List[str]:
        groups: List[str] = []
        i = 0
        while i < len(text):
            digit = text[i]
            if digit not in "0123456789":
                groups.append(digit)
                i += 1
                continue

            run_length = 1
            while i + run_length < len(text) and text[i + run_length] == digit:
                run_length += 1

            remaining = run_length
            while remaining > 0:
                length = min(remaining, 4)
                groups.append(digit * length)
                remaining -= length

            i += run_length

        return groups

    def _get_text_score(self, text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
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

    def _looks_like_french_text(self, text: str) -> bool:
        if not text or len(text) < 2:
            return False

        common_words = {
            "NORD",
            "SUD",
            "EST",
            "OUEST",
            "CACHE",
            "TRESOR",
            "COORDONNEES",
            "LATITUDE",
            "LONGITUDE",
            "DEGRES",
            "MINUTES",
            "SECONDES",
            "POINT",
            "LIEU",
            "ENDROIT",
            "ICI",
            "LA",
            "CHERCHER",
            "TROUVER",
            "BONJOUR",
            "SALUT",
            "MERCI",
            "BRAVO",
            "FELICITATIONS",
            "ENIGME",
            "MESSAGE",
            "TEXTE",
            "PHRASE",
            "MOT",
            "LETTRE",
            "CODE",
            "CHIFFRE",
            "TELEPHONE",
            "MOBILE",
            "APPEL",
            "NUMERO",
            "CLAVIER",
            "TOUCHE",
            "MULTITAP",
            "DECODE",
            "ENCODE",
            "CHIFFREMENT",
            "DECHIFFREMENT",
            "HELLO",
            "WORLD",
            "TEXT",
            "WORD",
            "LETTER",
            "NUMBER",
            "PHONE",
            "CALL",
            "GOODBYE",
            "MYSTERE",
            "SECRET",
            "SOLUTION",
            "REPONSE",
            "INDICE",
            "PISTE",
            "FINAL",
            "ETAPE",
            "NEXT",
            "SUIVANT",
            "FIN",
            "START",
            "DEBUT",
            "GCCODE",
        }

        text_upper = text.upper()
        for word in common_words:
            if word == text_upper:
                return True
            if word in text_upper and len(word) >= 4:
                return True

        french_patterns = ["QU", "CH", "PH", "TH", "OU", "ON", "AN", "EN", "IN", "UN", "ION", "TION"]
        if len(text_upper) >= 4 and any(pattern in text_upper for pattern in french_patterns):
            return True

        vowels = "AEIOUY"
        vowel_count = sum(1 for c in text_upper if c in vowels)
        if len(text_upper) >= 4:
            ratio = vowel_count / len(text_upper)
            if 0.25 <= ratio <= 0.6:
                return True

        return False

    def _detect_separator(self, text: str) -> str:
        if " " in text:
            return "space"
        if "-" in text:
            return "dash"
        return "none"

    def _detect(self, text: str) -> Tuple[bool, float]:
        if not text:
            return False, 0.0

        cleaned = re.sub(r"\s+", "", text)
        if not cleaned:
            return False, 0.0

        if not re.fullmatch(r"[0-9-\s]+", text):
            return False, 0.0

        digits = [c for c in cleaned if c.isdigit()]
        if not digits:
            return False, 0.0

        ratio = len(digits) / max(1, len(cleaned))
        has_valid_digit = any(d in "234567890" for d in digits)
        score = min(0.9, ratio * 0.8 + (0.1 if has_valid_digit else 0.0))
        return score > 0.3, score

    def _bruteforce(self, text: str) -> List[Dict[str, Any]]:
        solutions: List[Dict[str, Any]] = []
        separators = ["space", "dash", "none"]

        for sep in separators:
            try:
                if sep == "none":
                    segmentations = self._generate_all_segmentations(text, max_results=10)
                    for idx, segmentation in enumerate(segmentations, 1):
                        decoded = "".join(self._decode_table.get(code, "?") for code in segmentation)
                        unknown_count = decoded.count("?")
                        if decoded and (unknown_count / len(decoded)) < 0.3:
                            solutions.append(
                                {
                                    "separator": f"none_variant_{idx}",
                                    "decoded_text": decoded,
                                    "unknown_chars": unknown_count,
                                    "segmentation": segmentation,
                                }
                            )
                else:
                    decoded, unknown = self._decode(text, sep)
                    unknown_count = decoded.count("?")
                    if decoded and (unknown_count / len(decoded)) < 0.3:
                        solutions.append(
                            {
                                "separator": sep,
                                "decoded_text": decoded,
                                "unknown_chars": unknown_count,
                            }
                        )
            except Exception:
                continue

        return solutions

    def _calculate_confidence(self, solution: Dict[str, Any], enable_scoring: bool, context: Dict[str, Any]) -> float:
        decoded_text = solution["decoded_text"]
        separator = solution["separator"]
        unknown_chars = solution.get("unknown_chars", 0)

        if enable_scoring and self._scoring_available:
            fast_score = self._get_text_score_fast(decoded_text)
            if fast_score > 0:
                base_confidence = fast_score
                if "none_variant" in separator:
                    base_confidence *= 0.95
                elif separator == "space":
                    base_confidence *= 1.02
                return max(0.1, min(1.0, base_confidence))

        base_confidence = {
            "space": 0.9,
            "dash": 0.8,
            "none": 0.6,
        }.get(separator.split("_")[0], 0.5)

        if len(decoded_text) > 0:
            unknown_ratio = unknown_chars / len(decoded_text)
            base_confidence -= unknown_ratio * 0.5

        if self._looks_like_french_text(decoded_text):
            base_confidence += 0.1

        return max(0.1, min(1.0, base_confidence))

    def _get_plugin_info(self, start_time: float) -> Dict[str, Any]:
        execution_time = (time.time() - start_time) * 1000
        return {
            "name": self.name,
            "version": self.version,
            "execution_time": int(execution_time),
        }


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return MultitapCodePlugin().execute(inputs)
