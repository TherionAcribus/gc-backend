"""Tests unitaires pour le plugin bifid_delastelle."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("bifid_delastelle_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "BifidDelastellePlugin")


def test_encode_decode_roundtrip_simple() -> None:
    BifidDelastellePlugin = _load_plugin_class()
    plugin = BifidDelastellePlugin()

    encoded: Dict[str, Any] = plugin.execute({"text": "HELLO", "mode": "encode", "period": 5})
    assert encoded["status"] == "ok"
    cipher = encoded["results"][0]["text_output"]

    decoded: Dict[str, Any] = plugin.execute({"text": cipher, "mode": "decode", "period": 5})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == "HELLO"


def test_encode_decode_with_key() -> None:
    BifidDelastellePlugin = _load_plugin_class()
    plugin = BifidDelastellePlugin()

    encoded: Dict[str, Any] = plugin.execute({"text": "ATTACKATDAWN", "mode": "encode", "key": "SECRET", "period": 6})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"text": cipher, "mode": "decode", "key": "SECRET", "period": 6})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == "ATTACKATDAWN"


def test_grid_6x6_supports_digits() -> None:
    BifidDelastellePlugin = _load_plugin_class()
    plugin = BifidDelastellePlugin()

    plaintext = "HELLO123"
    encoded: Dict[str, Any] = plugin.execute({"text": plaintext, "mode": "encode", "grid_size": "6x6", "period": 4})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"text": cipher, "mode": "decode", "grid_size": "6x6", "period": 4})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == plaintext


def test_alphabet_mode_i_equals_j() -> None:
    BifidDelastellePlugin = _load_plugin_class()
    plugin = BifidDelastellePlugin()

    plaintext = "JIG"
    encoded: Dict[str, Any] = plugin.execute({"text": plaintext, "mode": "encode", "alphabet_mode": "I=J", "period": 3})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"text": cipher, "mode": "decode", "alphabet_mode": "I=J", "period": 3})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == "IIG"
