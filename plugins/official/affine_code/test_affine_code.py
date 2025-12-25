"""Tests unitaires pour le plugin affine_code.

Ces tests valident:
- L'encodage/décodage avec des paramètres (a, b)
- Le mode brute_force (retour de plusieurs résultats triés)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _load_plugin_class():
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("affine_code_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, "AffineCodePlugin")


def test_affine_code_encode_decode_roundtrip() -> None:
    AffineCodePlugin = _load_plugin_class()
    plugin = AffineCodePlugin()

    encoded: Dict[str, Any] = plugin.execute({"text": "ABC", "mode": "encode", "a": 5, "b": 8})
    assert encoded["status"] == "ok"
    assert encoded["results"]
    cipher_text = encoded["results"][0]["text_output"]

    decoded: Dict[str, Any] = plugin.execute({"text": cipher_text, "mode": "decode", "a": 5, "b": 8})
    assert decoded["status"] == "ok"
    assert decoded["results"]
    assert decoded["results"][0]["text_output"] == "ABC"


def test_affine_code_bruteforce_returns_sorted_results() -> None:
    AffineCodePlugin = _load_plugin_class()
    plugin = AffineCodePlugin()

    result: Dict[str, Any] = plugin.execute({"text": "IFMMP", "mode": "decode", "brute_force": True})
    assert result["status"] == "ok"
    assert isinstance(result.get("results"), list)
    assert len(result["results"]) > 10

    confidences = [r.get("confidence", 0) for r in result["results"]]
    assert confidences == sorted(confidences, reverse=True)
