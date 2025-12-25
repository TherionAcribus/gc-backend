from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("kenny_code_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "KennyCodePlugin")


def test_encode_decode_roundtrip_simple() -> None:
    KennyCodePlugin = _load_plugin_class()
    plugin = KennyCodePlugin()

    plaintext = "attack at dawn"
    encoded: Dict[str, Any] = plugin.execute({"mode": "encode", "text": plaintext})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"mode": "decode", "text": cipher})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == plaintext


def test_detect_kenny_code() -> None:
    KennyCodePlugin = _load_plugin_class()
    plugin = KennyCodePlugin()

    cipher = plugin.encode("abc")
    detected: Dict[str, Any] = plugin.execute({"mode": "detect", "text": cipher})
    assert detected["status"] == "ok"
    assert detected["results"][0]["metadata"]["is_match"] is True


def test_decode_strict_invalid_errors() -> None:
    KennyCodePlugin = _load_plugin_class()
    plugin = KennyCodePlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "decode", "text": "hello", "strict": "strict"})
    assert result["status"] == "error"
