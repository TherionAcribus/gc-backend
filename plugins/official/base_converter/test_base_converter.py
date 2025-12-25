"""Tests unitaires pour le plugin base_converter."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("base_converter_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "BaseConverterPlugin")


def test_base_converter_hex_to_ascii() -> None:
    BaseConverterPlugin = _load_plugin_class()
    plugin = BaseConverterPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "48656c6c6f", "mode": "decode", "source_base": "16", "target_base": "ascii"})
    assert result["status"] == "ok"
    assert result["results"][0]["text_output"].startswith("Hello")


def test_base_converter_ascii_to_hex() -> None:
    BaseConverterPlugin = _load_plugin_class()
    plugin = BaseConverterPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "Hi", "mode": "encode", "source_base": "ascii", "target_base": "16"})
    assert result["status"] == "ok"
    out = result["results"][0]["text_output"]
    # 'H'=0x48, 'i'=0x69
    assert out.lower() == "48 69"


def test_base_converter_autodetect_returns_results() -> None:
    BaseConverterPlugin = _load_plugin_class()
    plugin = BaseConverterPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "48656c6c6f", "mode": "autodetect", "embedded": False})
    assert result["status"] == "ok"
    assert len(result.get("results", [])) > 0
