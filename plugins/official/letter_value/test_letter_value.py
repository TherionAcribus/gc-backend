from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("letter_value_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "LetterValuePlugin")


def test_encode_simple() -> None:
    LetterValuePlugin = _load_plugin_class()
    plugin = LetterValuePlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "encode", "text": "ABC"})
    assert result["status"] == "ok"
    assert result["results"][0]["text_output"] == "1 2 3"


def test_decode_embedded_replaces_fragments() -> None:
    LetterValuePlugin = _load_plugin_class()
    plugin = LetterValuePlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "decode", "text": "N 48° 51.400", "embedded": True})
    assert result["status"] == "ok"
    # N -> 14 (le reste du texte est conservé)
    assert "14" in result["results"][0]["text_output"]


def test_detect_returns_ok() -> None:
    LetterValuePlugin = _load_plugin_class()
    plugin = LetterValuePlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "detect", "text": "HELLO"})
    assert result["status"] == "ok"
    assert "confidence" in result["results"][0]


def test_decode_bruteforce_returns_two_results() -> None:
    LetterValuePlugin = _load_plugin_class()
    plugin = LetterValuePlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "decode", "text": "ABC", "bruteforce": True})
    assert result["status"] == "ok"
    assert len(result["results"]) == 2


def test_decode_numeric_sequence_from_encode_output() -> None:
    LetterValuePlugin = _load_plugin_class()
    plugin = LetterValuePlugin()

    # 'CAT' -> 3 1 20 => '3 1 20' (avec séparateurs)
    encoded: Dict[str, Any] = plugin.execute({"mode": "encode", "text": "CAT"})
    assert encoded["status"] == "ok"
    assert encoded["results"][0]["text_output"] == "3 1 20"

    decoded_spaced: Dict[str, Any] = plugin.execute({"mode": "decode", "text": "3 1 20"})
    assert decoded_spaced["status"] == "ok"
    assert decoded_spaced["results"][0]["text_output"] == "CAT"

    decoded_concat: Dict[str, Any] = plugin.execute({"mode": "decode", "text": "3120"})
    assert decoded_concat["status"] == "ok"
    assert decoded_concat["results"][0]["text_output"] == "CAT"
