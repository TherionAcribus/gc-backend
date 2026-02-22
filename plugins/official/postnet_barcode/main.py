from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional


class PostnetBarcodePlugin:
    def __init__(self) -> None:
        self.name = "postnet_barcode"
        self.version = "1.2.1"

        try:
            from gc_backend.plugins.scoring import score_text

            self._score_text = score_text
            self._scoring_available = True
        except Exception:
            self._score_text = None
            self._scoring_available = False

        self.postnet_encoding = {
            "0": "11000",
            "1": "00011",
            "2": "00101",
            "3": "00110",
            "4": "01001",
            "5": "01010",
            "6": "01100",
            "7": "10001",
            "8": "10010",
            "9": "10100",
        }
        self.postnet_decoding = {v: k for k, v in self.postnet_encoding.items()}

    def validate_input(self, text: str, mode: str) -> tuple[bool, str, str]:
        if not text or not text.strip():
            return False, "Entrée vide", ""

        text = text.strip()

        if mode == "encode":
            digits = re.sub(r"[^0-9]", "", text)
            if not digits:
                return (
                    False,
                    "Aucun chiffre trouvé dans l'entrée. L'encodage POSTNET nécessite des chiffres.",
                    "",
                )
            return True, "", digits

        if mode == "decode":
            if re.search(r"[01|.\-_I╷]", text):
                return True, "", text
            return (
                False,
                "Format non reconnu. Le décodage POSTNET nécessite des barres (|, ., ╷) ou du binaire (0, 1).",
                "",
            )

        return True, "", text

    def detect_visual_format(self, text: str) -> str:
        has_binary = bool(re.search(r"[01]", text))
        has_pipe = bool(re.search(r"[|I]", text))
        has_dot = bool(re.search(r"[.\-_]", text))
        has_down = bool(re.search(r"╷", text))

        if has_binary and not (has_pipe or has_dot or has_down):
            return "binary"
        if has_pipe and has_dot and not has_down and not has_binary:
            return "pipe_dot"
        if has_pipe and has_down and not has_dot and not has_binary:
            return "pipe_down"
        if has_pipe or has_dot or has_down:
            return "mixed"
        return "unknown"

    def calculate_checksum(self, digits: str) -> str:
        total = sum(int(digit) for digit in digits)
        checksum = (10 - (total % 10)) % 10
        return str(checksum)

    def encode_to_postnet(
        self,
        digits: str,
        target_format: str = "auto",
        checksum_mode: str = "auto",
        frame_bars: str = "auto",
        visual_format: str = "auto",
    ) -> Dict[str, Any]:
        clean_digits = re.sub(r"[^0-9]", "", digits)
        if not clean_digits:
            raise ValueError("Aucun chiffre à encoder")

        if target_format == "auto":
            if len(clean_digits) == 5:
                target_format = "zip5"
            elif len(clean_digits) == 9:
                target_format = "zip9"
            elif len(clean_digits) == 11:
                target_format = "zip11"
            else:
                target_format = "free"

        if target_format == "zip5":
            clean_digits = clean_digits[:5].ljust(5, "0")
        elif target_format == "zip9":
            clean_digits = clean_digits[:9].ljust(9, "0")
        elif target_format == "zip11":
            clean_digits = clean_digits[:11].ljust(11, "0")

        add_checksum = False
        if checksum_mode == "auto":
            add_checksum = target_format in ["zip5", "zip9", "zip11"]
        elif checksum_mode in {"required", "optional"}:
            add_checksum = True

        if add_checksum:
            checksum = self.calculate_checksum(clean_digits)
            full_code = clean_digits + checksum
        else:
            checksum = None
            full_code = clean_digits

        add_frame_bars = False
        if frame_bars == "auto":
            add_frame_bars = target_format in ["zip5", "zip9", "zip11"]
        elif frame_bars == "always":
            add_frame_bars = True

        postnet_code = ""
        if add_frame_bars:
            postnet_code += "1"

        for digit in full_code:
            if digit in self.postnet_encoding:
                postnet_code += self.postnet_encoding[digit]
            else:
                raise ValueError(f"Caractère non supporté: {digit}")

        if add_frame_bars:
            postnet_code += "1"

        binary_format = postnet_code
        pipe_dot_format = self.format_to_visual(postnet_code, "pipe_dot")
        pipe_down_format = self.format_to_visual(postnet_code, "pipe_down")

        if visual_format == "binary":
            display_format = binary_format
        elif visual_format == "pipe_dot":
            display_format = pipe_dot_format
        elif visual_format == "pipe_down":
            display_format = pipe_down_format
        else:
            display_format = pipe_dot_format

        return {
            "binary": binary_format,
            "pipe_dot": pipe_dot_format,
            "pipe_down": pipe_down_format,
            "display": display_format,
            "digits": clean_digits,
            "full_code": full_code,
            "checksum": checksum,
            "has_checksum": add_checksum,
            "has_frame_bars": add_frame_bars,
            "format": target_format,
        }

    def format_to_visual(self, binary_code: str, visual_format: str) -> str:
        if visual_format == "binary":
            return binary_code
        if visual_format == "pipe_dot":
            return binary_code.replace("1", "|").replace("0", ".")
        if visual_format == "pipe_down":
            return binary_code.replace("1", "|").replace("0", "╷")
        return binary_code

    def normalize_barcode(self, barcode: str) -> str:
        clean = barcode.strip()
        if re.match(r"^[01]+$", clean):
            return clean

        normalized = ""
        for char in clean:
            if char in "|I1":
                normalized += "1"
            elif char in ".-_0╷":
                normalized += "0"
        return normalized

    def decode_from_postnet(self, barcode: str, flexible: bool = False) -> Dict[str, Any]:
        original_format = self.detect_visual_format(barcode)
        clean_barcode = self.normalize_barcode(barcode)

        if not clean_barcode:
            return {
                "success": False,
                "error": "Code barre vide ou invalide",
                "zip_code": None,
                "checksum_valid": None,
                "has_frame_bars": False,
                "has_checksum": False,
                "original_format": original_format,
            }

        has_frame_bars = clean_barcode.startswith("1") and clean_barcode.endswith("1")
        data_portion = clean_barcode[1:-1] if has_frame_bars else clean_barcode

        if len(data_portion) % 5 != 0:
            if not flexible:
                return {
                    "success": False,
                    "error": f"Longueur de données invalide: {len(data_portion)} (doit être multiple de 5)",
                    "zip_code": None,
                    "checksum_valid": None,
                    "has_frame_bars": has_frame_bars,
                    "has_checksum": False,
                    "original_format": original_format,
                }
            data_portion = data_portion[: len(data_portion) - (len(data_portion) % 5)]

        digits = ""
        invalid_patterns: List[str] = []

        for i in range(0, len(data_portion), 5):
            pattern = data_portion[i : i + 5]
            if pattern.count("1") != 2:
                if not flexible:
                    return {
                        "success": False,
                        "error": f"Pattern invalide: {pattern} (doit avoir exactement 2 barres hautes)",
                        "zip_code": None,
                        "checksum_valid": None,
                        "has_frame_bars": has_frame_bars,
                        "has_checksum": False,
                        "original_format": original_format,
                    }
                invalid_patterns.append(pattern)
                continue

            if pattern in self.postnet_decoding:
                digits += self.postnet_decoding[pattern]
            else:
                if not flexible:
                    return {
                        "success": False,
                        "error": f"Pattern non reconnu: {pattern}",
                        "zip_code": None,
                        "checksum_valid": None,
                        "has_frame_bars": has_frame_bars,
                        "has_checksum": False,
                        "original_format": original_format,
                    }
                invalid_patterns.append(pattern)
                continue

        if not digits:
            return {
                "success": False,
                "error": "Aucun chiffre décodé",
                "zip_code": None,
                "checksum_valid": None,
                "has_frame_bars": has_frame_bars,
                "has_checksum": False,
                "original_format": original_format,
            }

        checksum_valid = None
        has_checksum = False
        zip_digits = digits
        received_checksum = None
        calculated_checksum = None

        if len(digits) >= 2:
            test_zip_digits = digits[:-1]
            test_checksum = digits[-1]
            test_calculated = self.calculate_checksum(test_zip_digits)

            if test_checksum == test_calculated:
                zip_digits = test_zip_digits
                received_checksum = test_checksum
                calculated_checksum = test_calculated
                checksum_valid = True
                has_checksum = True
            elif not flexible and len(digits) in [6, 10, 12]:
                zip_digits = test_zip_digits
                received_checksum = test_checksum
                calculated_checksum = test_calculated
                checksum_valid = False
                has_checksum = True
            else:
                zip_digits = digits
                has_checksum = False

        if len(zip_digits) == 5:
            format_type = "ZIP-5"
        elif len(zip_digits) == 9:
            format_type = "ZIP+4"
        elif len(zip_digits) == 11:
            format_type = "ZIP+4+2"
        else:
            format_type = f"libre ({len(zip_digits)} chiffres)"

        result = {
            "success": True,
            "zip_code": zip_digits,
            "format": format_type,
            "checksum_received": received_checksum,
            "checksum_calculated": calculated_checksum,
            "checksum_valid": checksum_valid,
            "has_checksum": has_checksum,
            "has_frame_bars": has_frame_bars,
            "total_bars": len(clean_barcode),
            "flexible_mode": flexible,
            "original_format": original_format,
        }

        if invalid_patterns:
            result["invalid_patterns"] = invalid_patterns
            result["warning"] = f"{len(invalid_patterns)} pattern(s) invalide(s) ignoré(s)"

        return result

    def detect_postnet_pattern(self, text: str) -> List[Dict[str, Any]]:
        patterns: List[Dict[str, Any]] = []

        for match in re.finditer(r"[01]{10,}", text):
            if len(match.group()) % 5 == 0:
                patterns.append(
                    {
                        "type": "binary",
                        "text": match.group(),
                        "start": match.start(),
                        "end": match.end(),
                        "format": "binary",
                    }
                )

        for match in re.finditer(r"[|.\-_I]{10,}", text):
            if len(match.group()) % 5 == 0:
                patterns.append(
                    {
                        "type": "bars_dot",
                        "text": match.group(),
                        "start": match.start(),
                        "end": match.end(),
                        "format": "pipe_dot",
                    }
                )

        for match in re.finditer(r"[|╷I]{10,}", text):
            if len(match.group()) % 5 == 0:
                patterns.append(
                    {
                        "type": "bars_down",
                        "text": match.group(),
                        "start": match.start(),
                        "end": match.end(),
                        "format": "pipe_down",
                    }
                )

        for match in re.finditer(r"[|.\-_I╷01]{10,}", text):
            if len(match.group()) % 5 == 0:
                patterns.append(
                    {
                        "type": "mixed",
                        "text": match.group(),
                        "start": match.start(),
                        "end": match.end(),
                        "format": "mixed",
                    }
                )

        unique_patterns: List[Dict[str, Any]] = []
        seen_positions = set()

        for pattern in patterns:
            pos_key = (pattern["start"], pattern["end"])
            if pos_key not in seen_positions:
                seen_positions.add(pos_key)
                unique_patterns.append(pattern)

        return sorted(unique_patterns, key=lambda x: x["start"])

    def generate_bruteforce_variations(self, text: str) -> List[Dict[str, Any]]:
        variations: List[Dict[str, Any]] = []
        flexible_options = [True, False]
        frame_bar_assumptions = [True, False]

        for flexible in flexible_options:
            for assume_frame_bars in frame_bar_assumptions:
                try:
                    test_text = text

                    if assume_frame_bars and not (text.startswith("1") or text.startswith("|")):
                        normalized = self.normalize_barcode(text)
                        if normalized and not normalized.startswith("1"):
                            test_text = "1" + normalized + "1"
                            original_format = self.detect_visual_format(text)
                            if original_format == "pipe_dot":
                                test_text = self.format_to_visual(test_text, "pipe_dot")
                            elif original_format == "pipe_down":
                                test_text = self.format_to_visual(test_text, "pipe_down")

                    result = self.decode_from_postnet(test_text, flexible)

                    if result["success"]:
                        confidence = self.calculate_confidence(result, flexible, assume_frame_bars)
                        variation = {
                            "text_output": result["zip_code"],
                            "confidence": confidence,
                            "parameters": {
                                "flexible_mode": flexible,
                                "assumed_frame_bars": assume_frame_bars,
                                "original_has_frame_bars": result["has_frame_bars"],
                            },
                            "metadata": result,
                        }
                        variations.append(variation)
                except Exception:
                    continue

        unique_variations: List[Dict[str, Any]] = []
        seen_outputs = set()

        for var in variations:
            output_key = var["text_output"]
            if output_key not in seen_outputs:
                seen_outputs.add(output_key)
                unique_variations.append(var)

        unique_variations.sort(key=lambda x: x["confidence"], reverse=True)
        return unique_variations

    def calculate_confidence(self, decode_result: Dict[str, Any], flexible: bool, assumed_frame_bars: bool) -> float:
        base_confidence = 0.5

        if decode_result.get("checksum_valid") is True:
            base_confidence += 0.3
        elif decode_result.get("checksum_valid") is False:
            base_confidence -= 0.1

        if decode_result.get("format") in ["ZIP-5", "ZIP+4", "ZIP+4+2"]:
            base_confidence += 0.2

        if decode_result.get("has_frame_bars"):
            base_confidence += 0.1

        if flexible:
            base_confidence -= 0.1

        if assumed_frame_bars != decode_result.get("has_frame_bars"):
            base_confidence -= 0.05

        if decode_result.get("invalid_patterns"):
            base_confidence -= 0.1 * len(decode_result["invalid_patterns"])

        return max(0.0, min(1.0, base_confidence))

    def _get_text_score(self, text: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        if not self._scoring_available or not self._score_text:
            return None
        try:
            return self._score_text(text, context=context or {})
        except Exception:
            return None

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start_time = time.time()

        mode = str(inputs.get("mode", "decode")).lower()
        text = str(inputs.get("text", "")).strip()
        format_type = str(inputs.get("format", "auto")).lower()
        visual_format = str(inputs.get("visual_format", "auto")).lower()
        checksum_mode = str(inputs.get("checksum_mode", "auto")).lower()
        frame_bars = str(inputs.get("frame_bars", "auto")).lower()
        enable_scoring = bool(inputs.get("enable_scoring", True))
        is_bruteforce = bool(inputs.get("bruteforce", False) or inputs.get("brute_force", False))
        context = inputs.get("context", {})

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

        is_valid, error_message, cleaned_text = self.validate_input(text, mode)
        if not is_valid:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = error_message
            standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
            return standardized_response

        try:
            if mode == "encode":
                encode_result = self.encode_to_postnet(
                    cleaned_text, format_type, checksum_mode, frame_bars, visual_format
                )

                if encode_result["format"] == "free":
                    format_name = (
                        f"libre ({len(encode_result['digits'])} chiffres, "
                        f"{len(encode_result['binary'])} barres)"
                    )
                elif len(encode_result["binary"]) == 32:
                    format_name = "ZIP-5 (32 barres)"
                elif len(encode_result["binary"]) == 52:
                    format_name = "ZIP+4 (52 barres)"
                elif len(encode_result["binary"]) == 62:
                    format_name = "ZIP+4+2 (62 barres)"
                else:
                    format_name = f"personnalisé ({len(encode_result['binary'])} barres)"

                result = {
                    "id": "encode_result_1",
                    "text_output": encode_result["display"],
                    "confidence": 1.0,
                    "parameters": {
                        "mode": mode,
                        "format": format_type,
                        "visual_format": visual_format,
                        "checksum_mode": checksum_mode,
                        "frame_bars": frame_bars,
                        "input_digits": encode_result["digits"],
                    },
                    "metadata": {
                        "format_name": format_name,
                        "binary_representation": encode_result["binary"],
                        "pipe_dot_format": encode_result["pipe_dot"],
                        "pipe_down_format": encode_result["pipe_down"],
                        "total_bars": len(encode_result["binary"]),
                        "has_checksum": encode_result["has_checksum"],
                        "has_frame_bars": encode_result["has_frame_bars"],
                        "checksum": encode_result["checksum"],
                        "full_code": encode_result["full_code"],
                    },
                }

                standardized_response["results"].append(result)
                standardized_response["summary"]["best_result_id"] = "encode_result_1"
                standardized_response["summary"]["total_results"] = 1
                standardized_response["summary"]["message"] = f"Encodage réussi en {format_name}"

            elif mode == "decode":
                results: List[Dict[str, Any]] = []

                if is_bruteforce:
                    variations = self.generate_bruteforce_variations(text)
                    for i, variation in enumerate(variations, 1):
                        result = {
                            "id": f"bruteforce_result_{i}",
                            "text_output": variation["text_output"],
                            "confidence": variation["confidence"],
                            "parameters": {
                                "mode": mode,
                                "bruteforce": True,
                                **variation["parameters"],
                            },
                            "metadata": {
                                **variation["metadata"],
                                "bruteforce_variation": i,
                            },
                        }

                        zip_code = variation["text_output"]
                        if len(zip_code) == 9:
                            result["text_output"] = f"{zip_code[:5]}-{zip_code[5:]}"
                        elif len(zip_code) == 11:
                            result["text_output"] = f"{zip_code[:5]}-{zip_code[5:9]}-{zip_code[9:]}"

                        results.append(result)
                else:
                    patterns = self.detect_postnet_pattern(text)
                    if not patterns:
                        patterns = [
                            {"type": "direct", "text": text, "start": 0, "end": len(text), "format": "auto"}
                        ]

                    flexible_mode = (
                        format_type == "free"
                        or checksum_mode in ["optional", "none"]
                        or frame_bars == "never"
                    )

                    for i, pattern in enumerate(patterns, 1):
                        try:
                            decode_result = self.decode_from_postnet(pattern["text"], flexible_mode)
                            if not decode_result["success"]:
                                continue

                            confidence = self.calculate_confidence(decode_result, flexible_mode, False)

                            zip_code = decode_result["zip_code"]
                            formatted_zip = zip_code
                            if len(zip_code) == 9:
                                formatted_zip = f"{zip_code[:5]}-{zip_code[5:]}"
                            elif len(zip_code) == 11:
                                formatted_zip = f"{zip_code[:5]}-{zip_code[5:9]}-{zip_code[9:]}"

                            result = {
                                "id": f"decode_result_{i}",
                                "text_output": formatted_zip,
                                "confidence": confidence,
                                "parameters": {
                                    "mode": mode,
                                    "pattern_type": pattern["type"],
                                    "flexible_mode": flexible_mode,
                                    "original_format": pattern.get("format", "auto"),
                                },
                                "metadata": {
                                    **decode_result,
                                    "pattern_found": pattern["text"],
                                    "zip_code_raw": zip_code,
                                },
                            }

                            if enable_scoring:
                                scoring_result = self._get_text_score(formatted_zip, context)
                                if scoring_result:
                                    result["confidence"] = scoring_result.get("score", confidence)
                                    result["scoring"] = scoring_result

                            results.append(result)

                        except Exception:
                            continue

                if results:
                    results.sort(key=lambda x: x["confidence"], reverse=True)
                    standardized_response["results"] = results
                    standardized_response["summary"]["best_result_id"] = results[0]["id"]
                    standardized_response["summary"]["total_results"] = len(results)

                    best_result = results[0]
                    if is_bruteforce:
                        message = f"Bruteforce: {len(results)} variation(s) testée(s)"
                    else:
                        format_info = best_result.get("metadata", {}).get("format", "format inconnu")
                        if best_result.get("metadata", {}).get("checksum_valid") is True:
                            message = f"Décodage réussi: {format_info}"
                        elif best_result.get("metadata", {}).get("checksum_valid") is False:
                            message = f"Décodage avec checksum invalide: {format_info}"
                        else:
                            message = f"Décodage: {format_info}"

                        original_format = best_result.get("metadata", {}).get("original_format")
                        if original_format and original_format != "unknown":
                            message += f" (format {original_format})"

                    standardized_response["summary"]["message"] = message
                else:
                    standardized_response["status"] = "error"
                    standardized_response["summary"]["message"] = "Aucun code POSTNET valide détecté"
            else:
                standardized_response["status"] = "error"
                standardized_response["summary"]["message"] = f"Mode non supporté: {mode}"

        except Exception as exc:
            standardized_response["status"] = "error"
            standardized_response["summary"]["message"] = f"Erreur inattendue: {exc}"

        standardized_response["plugin_info"]["execution_time"] = int((time.time() - start_time) * 1000)
        return standardized_response


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return PostnetBarcodePlugin().execute(inputs)
