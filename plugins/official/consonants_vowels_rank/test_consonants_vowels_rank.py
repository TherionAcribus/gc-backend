"""Tests unitaires pour le plugin consonants_vowels_rank."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("consonants_vowels_rank_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "ConsonantsVowelsRankPlugin")


def test_roundtrip_both_variant() -> None:
    ConsonantsVowelsRankPlugin = _load_plugin_class()
    plugin = ConsonantsVowelsRankPlugin()

    plaintext = "HELLO"
    encoded: Dict[str, Any] = plugin.execute({"text": plaintext, "mode": "encode", "variant": "both"})
    assert encoded["status"] == "ok"
    cipher = encoded["results"][0]["text_output"]

    decoded: Dict[str, Any] = plugin.execute({"text": cipher, "mode": "decode", "variant": "both"})
    assert decoded["status"] == "ok"
    assert decoded["results"][0]["text_output"] == plaintext


def test_bruteforce_returns_three_results() -> None:
    ConsonantsVowelsRankPlugin = _load_plugin_class()
    plugin = ConsonantsVowelsRankPlugin()

    result: Dict[str, Any] = plugin.execute({"text": "C1V1", "mode": "bruteforce"})
    assert result["status"] == "ok"
    assert len(result["results"]) == 3
