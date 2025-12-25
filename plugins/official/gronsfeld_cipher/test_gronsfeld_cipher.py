"""Tests unitaires pour le plugin gronsfeld_cipher."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("gronsfeld_cipher_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "GronsfeldCipherPlugin")


def test_encode_decode_roundtrip() -> None:
    GronsfeldCipherPlugin = _load_plugin_class()
    plugin = GronsfeldCipherPlugin()

    plaintext = "BONJOUR LE MONDE"
    encoded: Dict[str, Any] = plugin.execute({"mode": "encode", "text": plaintext, "key": "1234"})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"mode": "decode", "text": cipher, "key": "1234"})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == plaintext


def test_key_is_cleaned_to_digits() -> None:
    GronsfeldCipherPlugin = _load_plugin_class()
    plugin = GronsfeldCipherPlugin()

    # La clé doit ignorer les caractères non numériques
    encoded: Dict[str, Any] = plugin.execute({"mode": "encode", "text": "ABC", "key": "1a2b3"})
    assert encoded["status"] == "ok"


def test_missing_key_errors() -> None:
    GronsfeldCipherPlugin = _load_plugin_class()
    plugin = GronsfeldCipherPlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "encode", "text": "ABC", "key": ""})
    assert result["status"] == "error"
