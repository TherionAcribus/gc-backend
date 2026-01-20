from __future__ import annotations

import re
import time
from typing import Any, Dict, List

try:
    from gc_backend.plugins.scoring import score_text

    _SCORING_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    score_text = None
    _SCORING_AVAILABLE = False


class ShadokNumbersPlugin:
    DIGIT_TO_SYLLABLE = {"0": "GA", "1": "BU", "2": "ZO", "3": "MEU"}
    SYLLABLE_TO_DIGIT = {
        "GA": "0",
        "BU": "1",
        "ZO": "2",
        "MEU": "3",
        "ME": "3",
    }

    def __init__(self) -> None:
        self.name = "shadok_numbers"
        self.version = "1.0.0"
        self.description = "Numération Shadok (GA BU ZO MEU)"

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _decimal_to_base4(n: int) -> str:
        if n == 0:
            return "0"
        digits: List[str] = []
        while n > 0:
            digits.append(str(n % 4))
            n //= 4
        return "".join(reversed(digits))

    def _encode_number(self, number_str: str) -> str:
        number_str = number_str.strip()
        if number_str == "":
            return ""
        try:
            n = int(number_str)
        except ValueError:
            raise ValueError(f"Valeur décimale invalide : {number_str}")
        if n < 0:
            raise ValueError("Les nombres négatifs ne sont pas supportés par la numération Shadok")

        base4 = self._decimal_to_base4(n)
        return "".join(self.DIGIT_TO_SYLLABLE[d] for d in base4)

    def _decode_token(self, token: str) -> str:
        token = token.upper()
        index = 0
        digits: List[str] = []
        pattern = re.compile(r"GA|BU|ZO|MEU|ME", re.IGNORECASE)
        while index < len(token):
            match = pattern.match(token, index)
            if not match:
                raise ValueError(f"Séquence Shadok invalide autour de '{token[index:index+4]}'")
            syll = match.group(0).upper()
            digits.append(self.SYLLABLE_TO_DIGIT[syll])
            index = match.end()

        decimal_value = 0
        for d in digits:
            decimal_value = decimal_value * 4 + int(d)
        return str(decimal_value)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def encode(self, text: str) -> str:
        tokens = re.split(r"[\s,;]+", text.strip())
        encoded_tokens = [self._encode_number(tok) for tok in tokens if tok]
        return " ".join(encoded_tokens)

    def decode(self, text: str) -> str:
        tokens = text.strip().split()
        if not tokens:
            tokens = [text.strip()]
        decoded_numbers = [self._decode_token(tok) for tok in tokens if tok]
        return " ".join(decoded_numbers)

    def _is_truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"true", "1", "yes", "on"}

    def _detect_fragments(self, text: str) -> Dict[str, Any]:
        """Find Shadok sequences and decode them for reference."""
        if not text:
            return {"fragments": [], "decoded_text": ""}

        pattern = re.compile(r"(GA|BU|ZO|MEU|ME)+", re.IGNORECASE)
        fragments = []
        for match in pattern.finditer(text):
            val = match.group(0)
            try:
                decoded_val = self._decode_token(val)
                fragments.append(
                    {
                        "value": val,
                        "start": match.start(),
                        "end": match.end(),
                        "decoded": decoded_val,
                    }
                )
            except ValueError:
                continue

        decoded_text = text
        # Replace from end to start to keep indices valid
        for frag in sorted(fragments, key=lambda f: f["start"], reverse=True):
            decoded_text = decoded_text[: frag["start"]] + frag["decoded"] + decoded_text[frag["end"] :]

        return {"fragments": fragments, "decoded_text": decoded_text}

    def _get_score(self, text: str, context: Dict[str, Any]) -> Dict[str, Any] | None:
        if not _SCORING_AVAILABLE or not score_text:
            return None
        try:
            return score_text(text, context=context)
        except Exception:
            return None

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()
        mode = str(inputs.get("mode", "encode")).lower()
        text = inputs.get("text", "")
        context = inputs.get("context", {})
        enable_scoring = self._is_truthy(inputs.get("enable_scoring", True))

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

        if mode not in {"encode", "decode"}:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Mode inconnu : {mode} (attendu: encode|decode)"
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        if not isinstance(text, str) or text.strip() == "":
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = "Aucun texte fourni à traiter."
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        try:
            if mode == "encode":
                result_text = self.encode(text)
                confidence = 1.0
                summary_msg = "Encodage Shadok réussi"
                scoring_info = self._get_score(result_text, context) if enable_scoring else None

            elif mode == "decode":
                result_text = self.decode(text)
                confidence = 0.95
                scoring_info = self._get_score(result_text, context) if enable_scoring else None
                if scoring_info and "score" in scoring_info:
                    confidence = float(scoring_info["score"])
                summary_msg = "Décodage Shadok réussi"

            elif mode == "detect":
                detection = self._detect_fragments(text)
                fragments = detection["fragments"]
                result_text = detection["decoded_text"]
                if not fragments:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = "Aucune séquence Shadok détectée"
                    standardized_response["plugin_info"]["execution_time"] = int(
                        (time.time() - start_time) * 1000
                    )
                    return standardized_response

                scoring_info = self._get_score(result_text, context) if enable_scoring else None
                confidence = scoring_info.get("score", 0.6) if scoring_info else 0.6
                summary_msg = f"{len(fragments)} fragment(s) Shadok détecté(s)"

                standardized_response["results"].append(
                    {
                        "id": "result_1",
                        "text_output": result_text,
                        "confidence": confidence,
                        "parameters": {"mode": "detect"},
                        "metadata": {
                            "fragments_count": len(fragments),
                            "fragments": fragments,
                        },
                        **({"scoring": scoring_info} if scoring_info else {}),
                    }
                )

                standardized_response["summary"].update(
                    {
                        "best_result_id": "result_1",
                        "total_results": 1,
                        "message": summary_msg,
                    }
                )

                standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                return standardized_response

            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode inconnu : {mode}"
                standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
                return standardized_response

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = str(exc)
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        result_entry = {
            "id": "result_1",
            "text_output": result_text,
            "confidence": confidence,
            "parameters": {"mode": mode},
            "metadata": {"processed_chars": len(text)},
        }
        if enable_scoring and "scoring_info" in locals() and scoring_info:
            result_entry["scoring"] = scoring_info

        standardized_response["results"].append(result_entry)

        standardized_response["summary"].update(
            {
                "best_result_id": "result_1",
                "total_results": 1,
                "message": summary_msg,
            }
        )

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return ShadokNumbersPlugin().execute(inputs)
