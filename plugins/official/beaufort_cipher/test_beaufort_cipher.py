"""Tests unitaires pour le plugin beaufort_cipher."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("beaufort_cipher_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "BeaufortCipherPlugin")


def test_beaufort_cipher_roundtrip() -> None:
    BeaufortCipherPlugin = _load_plugin_class()
    plugin = BeaufortCipherPlugin()

    encoded: Dict[str, Any] = plugin.execute({"text": "HELLO WORLD", "mode": "encode", "key": "FORT"})
    assert encoded["status"] == "ok"
    cipher = encoded["results"][0]["text_output"]

    decoded: Dict[str, Any] = plugin.execute({"text": cipher, "mode": "decode", "key": "FORT"})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == "HELLO WORLD"


def test_beaufort_cipher_requires_key() -> None:
    BeaufortCipherPlugin = _load_plugin_class()
    plugin = BeaufortCipherPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "HELLO", "mode": "encode", "key": ""})
    assert result["status"] == "error"
