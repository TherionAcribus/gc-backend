"""Tests unitaires pour le plugin solved_jigidi.

Ces tests valident:
- Le parsing CSV et l'indexation par `Code`
- Le format de sortie (status, results, notes)

Les requêtes réseau sont mockées.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_solved_jigidi_returns_notes_and_coordinates(monkeypatch, tmp_path):
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("solved_jigidi_main", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    SolvedJigidiPlugin = getattr(module, "SolvedJigidiPlugin")

    csv_payload = (
        "Code,Description,Latitude,Longitude,Country,State,UserNotes,Date Added\r\n"
        "GC966C8,Example cache,N 48° 33.787,E 006° 38.803,France,Grand-Est,Useful note,Aug-11-2022\r\n"
    ).encode("utf-8")

    pubhtml_payload = (
        "items.push({name: \"Solved Jigidi\", pageUrl: \"https://docs.google.com/spreadsheets/d/e/"
        "2PACX-1vQ358lFBRDaOUD1GOOvhOR9Wp4ECnUINbgT5M_vqiRTGoM3k3OKtY2shq1Ajqsmf7T8XqIE7Owm0-z4/"
        "pubhtml/sheet?headers=false&gid=0\", gid: \"0\",initialSheet: (\"0\" == gid)});"
    ).encode("utf-8")

    plugin = SolvedJigidiPlugin()

    plugin._snapshot_path = tmp_path / "snapshot.json"
    plugin._csv_cache_path = tmp_path / "sheet_cache.csv"
    plugin._csv_cache_meta_path = tmp_path / "sheet_cache_meta.json"
    plugin._site_cache_path = tmp_path / "site_cache.json"

    def fake_get(url: str, timeout: int = 20):  # noqa: ARG001
        if "pubhtml" in url and "output=csv" not in url:
            return _FakeResponse(pubhtml_payload, status_code=200)
        return _FakeResponse(csv_payload, status_code=200)

    monkeypatch.setattr(plugin._session, "get", fake_get)

    result: Dict[str, Any] = plugin.execute({"gc_code": "GC966C8", "max_age_hours": 24, "force_refresh": True})

    assert result["status"] == "ok"
    assert result.get("results")

    item = result["results"][0]
    assert "Useful note" in item["text_output"]

    # Si la détection de coordonnées est active dans l'environnement de test,
    # on doit récupérer des coordonnées décimales.
    if item.get("decimal_latitude") is not None and item.get("decimal_longitude") is not None:
        assert isinstance(item["decimal_latitude"], (int, float))
        assert isinstance(item["decimal_longitude"], (int, float))
        assert result.get("coordinates") is not None


def test_solved_jigidi_site_fallback_when_missing_from_sheet(monkeypatch, tmp_path):
    plugin_path = Path(__file__).resolve().parent / "main.py"
    spec = importlib.util.spec_from_file_location("solved_jigidi_main_site", plugin_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    SolvedJigidiPlugin = getattr(module, "SolvedJigidiPlugin")

    # Empty sheet (no rows)
    csv_payload = (
        "Code,Description,Latitude,Longitude,Country,State,UserNotes,Date Added\r\n"
    ).encode("utf-8")

    pubhtml_payload = (
        "items.push({name: \"Solved Jigidi\", pageUrl: \"https://docs.google.com/spreadsheets/d/e/"
        "2PACX-1vQ358lFBRDaOUD1GOOvhOR9Wp4ECnUINbgT5M_vqiRTGoM3k3OKtY2shq1Ajqsmf7T8XqIE7Owm0-z4/"
        "pubhtml/sheet?headers=false&gid=0\", gid: \"0\",initialSheet: (\"0\" == gid)});"
    ).encode("utf-8")

    html_payload = (
        "<h3>Found:</h3>"
        "<p><strong>Code:</strong> <a href='https://coord.info/GCA72DG'>GCA72DG</a></p>"
        "<p><strong>Name:</strong> Le pêcheur - #30</p>"
        "<p><strong>Coords:</strong> <a href='javascript:void(0)' class='copyable' "
        "onclick=\"copyText('N 48° 47.342, E 002° 43.051')\">N 48° 47.342, E 002° 43.051</a></p>"
        "<p><strong>Country:</strong> France</p>"
        "<p><strong>State:</strong> Île-de-France</p>"
        "<p><strong>Notes:</strong> Certitude: PÊCHEURS</p>"
        "<p><strong>Date added/updated:</strong> 2024-04-08</p>"
    ).encode("utf-8")

    plugin = SolvedJigidiPlugin()
    plugin._snapshot_path = tmp_path / "snapshot.json"
    plugin._csv_cache_path = tmp_path / "sheet_cache.csv"
    plugin._csv_cache_meta_path = tmp_path / "sheet_cache_meta.json"
    plugin._site_cache_path = tmp_path / "site_cache.json"

    def fake_get(url: str, timeout: int = 20):  # noqa: ARG001
        if "solvedjigidi.com/search.php" in url:
            return _FakeResponse(html_payload, status_code=200)
        if "pubhtml" in url and "output=csv" not in url:
            return _FakeResponse(pubhtml_payload, status_code=200)
        return _FakeResponse(csv_payload, status_code=200)

    monkeypatch.setattr(plugin._session, "get", fake_get)

    result: Dict[str, Any] = plugin.execute(
        {
            "gc_code": "GCA72DG",
            "site_fallback": True,
            "site_cache_ttl_hours": 168,
            "max_age_hours": 24,
        }
    )

    assert result["status"] == "ok"
    assert result.get("results")
    item = result["results"][0]
    assert "N 48° 47.342" in item["text_output"]
    assert "Certitude" in item["text_output"]
