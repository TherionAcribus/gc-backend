from __future__ import annotations

import re
import time
from typing import Any, Dict, List

try:
    from gc_backend.plugins.scoring import score_text, score_text_fast

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text = None
    score_text_fast = None
    _SCORING_AVAILABLE = False


class WolseleyCipherPlugin:
    """Encode/decode using the Wolseley cipher (reversible substitution)."""

    def __init__(self) -> None:
        self.name = "wolseley_cipher"
        self.version = "1.0.0"
        self.base_alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    # ------------------------------------------------------------------
    # Core Wolseley logic
    # ------------------------------------------------------------------
    def _generate_alphabet(self, key: str, removed_letter: str = "J") -> str:
        clean_key = "".join(dict.fromkeys(str(key).upper().replace(" ", "")))
        alphabet = self.base_alphabet
        if removed_letter != "none" and removed_letter in alphabet:
            alphabet = alphabet.replace(removed_letter, "")

        deranged = ""
        for char in clean_key:
            if char in alphabet and char not in deranged:
                deranged += char
        for char in alphabet:
            if char not in deranged:
                deranged += char
        return deranged

    @staticmethod
    def _create_substitution_table(alphabet: str) -> Dict[str, str]:
        length = len(alphabet)
        return {char: alphabet[length - 1 - idx] for idx, char in enumerate(alphabet)}

    def encode(self, text: str, key: str = "", removed_letter: str = "J") -> str:
        if not key:
            alphabet = self.base_alphabet
            if removed_letter != "none" and removed_letter in alphabet:
                alphabet = alphabet.replace(removed_letter, "")
        else:
            alphabet = self._generate_alphabet(key, removed_letter)

        substitution_table = self._create_substitution_table(alphabet)
        result = []
        for char in text.upper():
            if char in substitution_table:
                result.append(substitution_table[char])
            else:
                result.append(char)
        return "".join(result)

    def decode(self, text: str, key: str = "", removed_letter: str = "J") -> str:
        return self.encode(text, key, removed_letter)

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"[^A-Z]", "", text.upper())

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
            cleaned = self._clean_text(text)
            return score_text(cleaned, context=context)
        except Exception:
            return None

    def _get_score_fast(self, text: str) -> float:
        if not _SCORING_AVAILABLE or not score_text_fast:
            return 0.3
        try:
            return score_text_fast(self._clean_text(text))
        except Exception:
            return 0.3

    # ------------------------------------------------------------------
    # Bruteforce
    # ------------------------------------------------------------------
    def bruteforce(
        self,
        text: str,
        removed_letter: str,
        candidate_keys: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        common_keys = [
            "",
            "SECRET",
            "CIPHER",
            "CODE",
            "KEY",
            "WOLSELEY",
            "ALPHABET",
            "ENIGMA",
            "CRYPTO",
            "DECODE",
            "MYSTERE",
            "TRESOR",
            "CACHE",
            "GEOCACHING",
            "MYSTERY",
        ]
        keys = candidate_keys or common_keys
        results: List[Dict[str, Any]] = []
        for key in keys:
            decoded_text = self.decode(text, key, removed_letter)
            if any(r["decoded_text"] == decoded_text for r in results):
                continue
            results.append({"key": key, "decoded_text": decoded_text})
        return results

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        mode = str(inputs.get("mode", "decode")).lower()
        text = inputs.get("text", "")
        key = inputs.get("key", "")
        removed_letter = str(inputs.get("removed_letter", "J"))
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))
        context = inputs.get("context", {})
        max_results = min(int(inputs.get("max_results", 10) or 10), 50)
        do_bruteforce = mode == "bruteforce" or self._is_truthy(inputs.get("bruteforce", False))
        candidate_keys = self._parse_candidate_keys(inputs.get("candidate_keys"))

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
                encoded = self.encode(text, key, removed_letter)
                response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": encoded,
                        "confidence": 1.0,
                        "parameters": {
                            "mode": mode,
                            "key": key or "(aucune clé - Atbash)",
                            "removed_letter": removed_letter,
                        },
                        "metadata": {
                            "processed_chars": len(text),
                            "alphabet_used": self._generate_alphabet(key, removed_letter),
                        },
                    }
                )
                response["summary"].update(
                    {"best_result_id": "result_1", "total_results": 1, "message": "Encodage réussi"}
                )

            elif mode == "decode" and not do_bruteforce:
                decoded = self.decode(text, key, removed_letter)
                confidence = 0.5
                scoring_result = self._get_score(decoded, context) if enable_scoring else None
                if scoring_result and "score" in scoring_result:
                    confidence = float(scoring_result["score"])

                result = {
                    "id": "result_1",
                    "text_output": decoded,
                    "confidence": confidence,
                    "parameters": {
                        "mode": mode,
                        "key": key or "(aucune clé - Atbash)",
                        "removed_letter": removed_letter,
                    },
                    "metadata": {
                        "processed_chars": len(text),
                        "alphabet_used": self._generate_alphabet(key, removed_letter),
                    },
                }
                if scoring_result:
                    result["scoring"] = scoring_result

                response["results"].append(result)
                response["summary"].update(
                    {"best_result_id": "result_1", "total_results": 1, "message": "Décodage réussi"}
                )

            elif do_bruteforce:
                solutions = self.bruteforce(text, removed_letter, candidate_keys=candidate_keys)
                for idx, sol in enumerate(solutions[:max_results], 1):
                    confidence = self._get_score_fast(sol["decoded_text"]) if enable_scoring else 0.3
                    result = {
                        "id": f"result_{idx}",
                        "text_output": sol["decoded_text"],
                        "confidence": confidence,
                        "parameters": {
                            "mode": "decode",
                            "key": sol["key"] or "(aucune clé - Atbash)",
                            "removed_letter": removed_letter,
                            "bruteforce": True,
                        },
                        "metadata": {
                            "alphabet_used": self._generate_alphabet(sol["key"], removed_letter),
                        },
                    }
                    response["results"].append(result)

                response["results"].sort(key=lambda r: r.get("confidence", 0.0), reverse=True)
                response["summary"].update(
                    {
                        "best_result_id": response["results"][0]["id"] if response["results"] else None,
                        "total_results": len(response["results"]),
                        "message": f"{len(response['results'])} solutions générées",
                    }
                )

            elif mode == "detect":
                detect_keys = candidate_keys or [key or ""]
                best_result = None
                for candidate_key in detect_keys:
                    decoded = self.decode(text, candidate_key, removed_letter)
                    confidence = 0.4
                    scoring_result = self._get_score(decoded, context) if enable_scoring else None
                    if scoring_result and "score" in scoring_result:
                        confidence = float(scoring_result["score"])

                    result = {
                        "id": "result_1",
                        "text_output": decoded,
                        "confidence": confidence,
                        "parameters": {
                            "mode": "detect",
                            "key": candidate_key or "(aucune clé - Atbash)",
                            "removed_letter": removed_letter,
                        },
                        "metadata": {
                            "processed_chars": len(text),
                            "alphabet_used": self._generate_alphabet(candidate_key, removed_letter),
                        },
                    }
                    if scoring_result:
                        result["scoring"] = scoring_result

                    if best_result is None or result["confidence"] > best_result["confidence"]:
                        best_result = result

                if best_result is None:
                    response["status"] = "error"
                    response["summary"]["message"] = "Aucun résultat détecté"
                    response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                    return response

                response["results"].append(best_result)
                response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Détection Wolseley effectuée",
                    }
                )

            else:
                response["status"] = "error"
                response["summary"]["message"] = f"Mode '{mode}' non pris en charge."

        except Exception as exc:
            response["status"] = "error"
            response["summary"]["message"] = f"Erreur: {exc}"

        response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return WolseleyCipherPlugin().execute(inputs)
