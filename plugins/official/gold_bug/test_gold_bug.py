"""Tests unitaires pour le plugin gold_bug."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("gold_bug_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "GoldBugPlugin")


def test_encode_decode_roundtrip() -> None:
    GoldBugPlugin = _load_plugin_class()
    plugin = GoldBugPlugin()

    plaintext = "GOLD BUG"
    encoded: Dict[str, Any] = plugin.execute({"mode": "encode", "text": plaintext})
    assert encoded["status"] == "ok"

    cipher = encoded["results"][0]["text_output"]
    decoded: Dict[str, Any] = plugin.execute({"mode": "decode", "text": cipher})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == plaintext


def test_detect_symbols() -> None:
    GoldBugPlugin = _load_plugin_class()
    plugin = GoldBugPlugin()

    result: Dict[str, Any] = plugin.execute({"mode": "detect", "text": "5 2 - "})
    assert result["status"] == "ok"
    assert "detection_score" in result["results"][0]["metadata"]
