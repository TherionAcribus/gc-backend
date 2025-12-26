from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class WrittenCoordinatesResult:
    exist: bool
    primary_coordinates: Optional[Dict[str, Any]]
    candidates: List[Dict[str, Any]]
    combined_results: Dict[str, Any]


class WrittenCoordinatesService:
    def __init__(self, plugin_manager):
        self._plugin_manager = plugin_manager

    def find(
        self,
        text: str,
        *,
        languages: Optional[List[str]] = None,
        max_candidates: int = 20,
        include_deconcat: bool = True,
        origin_coords: Optional[Any] = None,
    ) -> WrittenCoordinatesResult:
        langs = languages or ["fr"]
        plugin_inputs: Dict[str, Any] = {
            "text": text,
            "languages": langs,
            "max_candidates": max_candidates,
            "include_deconcat": include_deconcat,
        }
        if origin_coords is not None:
            plugin_inputs["origin_coords"] = origin_coords

        result = self._plugin_manager.execute_plugin("written_coords_converter", plugin_inputs)

        candidates = (result or {}).get("results") or []
        combined = (result or {}).get("combined_results") or {}
        primary = (result or {}).get("primary_coordinates")
        exist = bool(primary and isinstance(primary, dict) and primary.get("exist"))

        return WrittenCoordinatesResult(
            exist=exist,
            primary_coordinates=primary if exist else None,
            candidates=candidates if isinstance(candidates, list) else [],
            combined_results=combined if isinstance(combined, dict) else {},
        )
