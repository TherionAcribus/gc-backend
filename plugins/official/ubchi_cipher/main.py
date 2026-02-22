from __future__ import annotations

import math
import random
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


class UbchiCipherPlugin:
    """Ubchi cipher (double columnar transposition with null letters)."""

    def __init__(self) -> None:
        self.name = "ubchi_cipher"
        self.version = "1.0.0"

    # ------------------------------------------------------------------
    # Columnar helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _col_order(keyword: str) -> List[int]:
        keyword = keyword.upper()
        enumerated = list(enumerate(keyword))
        sorted_cols = sorted(enumerated, key=lambda x: (x[1], x[0]))
        return [idx for idx, _ in sorted_cols]

    def _columnar_transpose(self, text: str, keyword: str) -> str:
        kw_len = len(keyword)
        order = self._col_order(keyword)
        rows = math.ceil(len(text) / kw_len)
        padded_text = text.ljust(rows * kw_len)
        grid = [padded_text[i : i + kw_len] for i in range(0, len(padded_text), kw_len)]
        cipher = "".join("".join(row[col] for row in grid) for col in order)
        return cipher.rstrip()

    def _columnar_transpose_inverse(self, cipher: str, keyword: str) -> str:
        kw_len = len(keyword)
        order = self._col_order(keyword)
        rows = math.ceil(len(cipher) / kw_len)
        full_columns = len(cipher) % kw_len
        if full_columns == 0:
            full_columns = kw_len
        col_lengths = [rows if i < full_columns else rows - 1 for i in range(kw_len)]
        segments: Dict[int, str] = {}
        idx = 0
        for col in order:
            length = col_lengths[col]
            segments[col] = cipher[idx : idx + length]
            idx += length
        plaintext_chars: List[str] = []
        for r in range(rows):
            for c in range(kw_len):
                segment = segments.get(c, "")
                if r < len(segment):
                    plaintext_chars.append(segment[r])
        return "".join(plaintext_chars).rstrip()

    # ------------------------------------------------------------------
    # Ubchi core
    # ------------------------------------------------------------------
    def encode(self, text: str, keyword: str, null_letters: int = 1) -> str:
        text = text.replace(" ", "").upper()
        inter = self._columnar_transpose(text, keyword)
        nulls = "".join(random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(null_letters))
        inter2 = inter + nulls
        final = self._columnar_transpose(inter2, keyword)
        return final

    def decode(self, cipher: str, keyword: str, null_letters: int = 1) -> str:
        step1 = self._columnar_transpose_inverse(cipher, keyword)
        if null_letters:
            step1 = step1[:-null_letters] if len(step1) >= null_letters else ""
        plaintext = self._columnar_transpose_inverse(step1, keyword)
        return plaintext

    # ------------------------------------------------------------------
    # Bruteforce (sampled permutations)
    # ------------------------------------------------------------------
    def bruteforce(self, cipher: str, max_key_length: int = 6, null_letters: int = 1) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for key_len in range(3, max_key_length + 1):
            permutations = list({"".join(random.sample(alphabet, key_len)) for _ in range(500)})
            for keyword in permutations:
                decoded = self.decode(cipher, keyword, null_letters)
                results.append({"keyword": keyword, "decoded_text": decoded})
        return results

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r"[^A-Z]", "", text.upper())

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

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        text = inputs.get("text", "")
        mode = str(inputs.get("mode", "decode")).lower()
        keyword = str(inputs.get("keyword", "UBER"))
        null_letters = int(inputs.get("null_letters", 1) or 1)
        context = inputs.get("context", {})
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))
        do_bruteforce = mode == "bruteforce" or self._is_truthy(inputs.get("bruteforce", False))

        response = {
            "status": "success",
            "plugin_info": {"name": self.name, "version": self.version, "execution_time": 0},
            "inputs": inputs.copy(),
            "results": [],
            "summary": {"best_result_id": None, "total_results": 0, "message": ""},
        }

        if not text:
            response["status"] = "error"
            response["summary"]["message"] = "Aucun texte fourni"
            response["plugin_info"]["execution_time"] = int((time.time() - start) * 1000)
            return response

        try:
            if mode == "encode":
                cipher = self.encode(text, keyword, null_letters)
                response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": cipher,
                        "confidence": 1.0,
                        "parameters": {"mode": mode, "keyword": keyword, "null_letters": null_letters},
                        "metadata": {"processed_chars": len(text)},
                    }
                )
                response["summary"].update(
                    {"best_result_id": "result_1", "total_results": 1, "message": "Encodage réussi"}
                )

            elif mode == "decode" and not do_bruteforce:
                plaintext = self.decode(text, keyword, null_letters)
                confidence = 0.5
                score_info = self._get_score(plaintext, context) if enable_scoring else None
                if score_info and "score" in score_info:
                    confidence = float(score_info["score"])
                result = {
                    "id": "result_1",
                    "text_output": plaintext,
                    "confidence": confidence,
                    "parameters": {"mode": mode, "keyword": keyword, "null_letters": null_letters},
                    "metadata": {"processed_chars": len(text)},
                }
                if score_info:
                    result["scoring"] = score_info
                response["results"].append(result)
                response["summary"].update(
                    {"best_result_id": "result_1", "total_results": 1, "message": "Décodage réussi"}
                )

            elif do_bruteforce:
                solutions = self.bruteforce(text, max_key_length=5, null_letters=null_letters)
                for idx, sol in enumerate(solutions, 1):
                    confidence = self._get_score_fast(sol["decoded_text"]) if enable_scoring else 0.3
                    res = {
                        "id": f"result_{idx}",
                        "text_output": sol["decoded_text"],
                        "confidence": confidence,
                        "parameters": {"mode": "decode", "keyword": sol["keyword"], "null_letters": null_letters},
                        "metadata": {},
                    }
                    response["results"].append(res)

                response["results"].sort(key=lambda r: r.get("confidence", 0), reverse=True)
                response["summary"].update(
                    {
                        "best_result_id": response["results"][0]["id"] if response["results"] else None,
                        "total_results": len(response["results"]),
                        "message": f"{len(response['results'])} solutions générées",
                    }
                )

            elif mode == "detect":
                cleaned = self._clean_text(text)
                if not cleaned:
                    response["status"] = "error"
                    response["summary"]["message"] = "Aucun texte exploitable pour la détection"
                    response["plugin_info"]["execution_time"] = int((time.time() - start) * 1000)
                    return response

                # Heuristic: try decoding with provided keyword and compare scoring
                decoded = self.decode(cleaned, keyword, null_letters)
                confidence = 0.4
                score_info = self._get_score(decoded, context) if enable_scoring else None
                if score_info and "score" in score_info:
                    confidence = float(score_info["score"])

                response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": decoded,
                        "confidence": confidence,
                        "parameters": {
                            "mode": "detect",
                            "keyword": keyword,
                            "null_letters": null_letters,
                        },
                        "metadata": {
                            "processed_chars": len(text),
                            "cleaned_length": len(cleaned),
                        },
                    }
                )
                if score_info:
                    response["results"][0]["scoring"] = score_info
                response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": "Détection Ubchi effectuée",
                    }
                )

            else:
                response["status"] = "error"
                response["summary"]["message"] = f"Mode inconnu: {mode}"

        except Exception as exc:
            response["status"] = "error"
            response["summary"]["message"] = f"Erreur: {exc}"

        response["plugin_info"]["execution_time"] = int((time.time() - start) * 1000)
        return response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return UbchiCipherPlugin().execute(inputs)
