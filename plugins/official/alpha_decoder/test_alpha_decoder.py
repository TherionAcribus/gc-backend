"""Tests unitaires pour le plugin alpha_decoder.

Ces tests valident:
- Encodage simple (A=1...)
- Décodage simple (1=A...)
- Gestion du paramètre offset
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("alpha_decoder_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "AlphaDecoderPlugin")


def test_alpha_decoder_encode_basic() -> None:
    AlphaDecoderPlugin = _load_plugin_class()
    plugin = AlphaDecoderPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "ABC", "mode": "encode", "offset": 0})
    assert result["status"] == "ok"
    assert result["results"][0]["text_output"] == "1 2 3"


def test_alpha_decoder_decode_basic() -> None:
    AlphaDecoderPlugin = _load_plugin_class()
    plugin = AlphaDecoderPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "1 2 3", "mode": "decode", "offset": 0})
    assert result["status"] == "ok"
    assert result["results"][0]["text_output"] == "A B C"


def test_alpha_decoder_offset() -> None:
    AlphaDecoderPlugin = _load_plugin_class()
    plugin = AlphaDecoderPlugin()

    encoded: Dict[str, Any] = plugin.execute({"text": "A", "mode": "encode", "offset": 1})
    assert encoded["status"] == "ok"
    assert encoded["results"][0]["text_output"] == "26"

    decoded: Dict[str, Any] = plugin.execute({"text": "26", "mode": "decode", "offset": 1})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == "A"
