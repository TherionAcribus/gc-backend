"""Tests unitaires pour le plugin atbash."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("atbash_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "AtbashPlugin")


def test_atbash_encode_decode_roundtrip() -> None:
    AtbashPlugin = _load_plugin_class()
    plugin = AtbashPlugin()

    encoded: Dict[str, Any] = plugin.execute({"text": "HELLO WORLD", "mode": "encode"})
    assert encoded["status"] == "ok"
    cipher = encoded["results"][0]["text_output"]
    assert cipher == "SVOOL DLIOW"

    decoded: Dict[str, Any] = plugin.execute({"text": cipher, "mode": "decode"})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == "HELLO WORLD"


def test_atbash_strict_rejects_non_allowed_chars() -> None:
    AtbashPlugin = _load_plugin_class()
    plugin = AtbashPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "SVOOL@DLIOW", "mode": "decode", "strict": "strict", "allowed_chars": " "})
    assert result["status"] == "error"


def test_atbash_detect_strict() -> None:
    AtbashPlugin = _load_plugin_class()
    plugin = AtbashPlugin()

    ok: Dict[str, Any] = plugin.execute({"text": "SVOOL DLIOW", "mode": "detect", "strict": "strict", "allowed_chars": " "})
    assert ok["status"] == "ok"
    assert ok["results"][0]["metadata"]["is_match"] is True

    ko: Dict[str, Any] = plugin.execute({"text": "SVOOL@DLIOW", "mode": "detect", "strict": "strict", "allowed_chars": " "})
    assert ko["status"] == "ok"
    assert ko["results"][0]["metadata"]["is_match"] is False
