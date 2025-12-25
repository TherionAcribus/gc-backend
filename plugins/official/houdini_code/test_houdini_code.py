from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("houdini_code_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "HoudiniCodePlugin")


def test_encode_decode_numbers_roundtrip() -> None:
    HoudiniCodePlugin = _load_plugin_class()
    plugin = HoudiniCodePlugin()

    plaintext = "1230"
    encoded: Dict[str, Any] = plugin.execute({"mode": "encode", "text": plaintext})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"mode": "decode", "text": cipher, "output_format": "numbers"})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == plaintext


def test_encode_decode_letters_roundtrip() -> None:
    HoudiniCodePlugin = _load_plugin_class()
    plugin = HoudiniCodePlugin()

    plaintext = "ABJ"
    encoded: Dict[str, Any] = plugin.execute({"mode": "encode", "text": plaintext})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"mode": "decode", "text": cipher, "output_format": "letters"})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == plaintext


def test_decode_bruteforce_returns_two_results() -> None:
    HoudiniCodePlugin = _load_plugin_class()
    plugin = HoudiniCodePlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "decode", "text": "Pray Answer", "bruteforce": True})
    assert result["status"] == "ok"
    assert len(result["results"]) == 2


def test_decode_strict_unknown_word_errors() -> None:
    HoudiniCodePlugin = _load_plugin_class()
    plugin = HoudiniCodePlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "decode", "text": "UnknownWord", "strict": "strict"})
    assert result["status"] == "error"
