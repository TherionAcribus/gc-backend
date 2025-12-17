"""Official plugin: coordinate_projection.

This plugin projects a GPS coordinate from an origin point using a distance and a bearing
(azimuth from North) and returns the resulting coordinate in Geocaching format.

It supports:
- strict mode: use explicit inputs (origin_coords + distance + bearing)
- smooth mode: extract distance/bearing from a free text description
"""

import re
import time
import math
from typing import Any, Dict, Optional, Tuple


try:
    from gc_backend.blueprints.coordinates import detect_gps_coordinates, convert_ddm_to_decimal
except Exception:
    detect_gps_coordinates = None
    convert_ddm_to_decimal = None


class CoordinateProjectionPlugin:
    """Plugin that projects a point based on distance and azimuth."""

    def __init__(self):
        self.name = "coordinate_projection"
        self.version = "1.0.0"
        self.earth_radius_m = 6_371_000.0

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the plugin.

        Args:
            inputs: Plugin inputs.

        Returns:
            Standardized plugin API v2.0 response.
        """
        start_time = time.time()

        text = str(inputs.get("text", "") or "")
        origin_coords = str(inputs.get("origin_coords", "") or "")
        strict = str(inputs.get("strict", "smooth") or "smooth").lower() == "strict"
        mode = str(inputs.get("mode", "decode") or "decode").lower()
        enable_gps_detection = bool(inputs.get("enable_gps_detection", True))

        if mode not in {"decode", "detect"}:
            return self._error_response(start_time, f"Mode inconnu: {mode}")

        origin_ddm, origin_lat, origin_lon = self._resolve_origin(origin_coords, text)
        if origin_ddm is None or origin_lat is None or origin_lon is None:
            return self._error_response(
                start_time,
                "Coordonnées d'origine introuvables. Fournis origin_coords ou inclue des coordonnées dans le texte.",
            )

        if mode == "detect":
            if strict:
                distance = self._safe_float(inputs.get("distance"), default=None)
                bearing = self._safe_float(inputs.get("bearing_deg"), default=None)
                unit = str(inputs.get("distance_unit", "m") or "m")

                if distance is None or bearing is None:
                    return self._error_response(start_time, "Distance et/ou azimut manquants en mode strict")

                dist_m = self._convert_distance_to_meters(distance, unit)
                summary = "Projection détectée (mode strict)"
                return self._ok_response(
                    start_time,
                    summary,
                    [
                        {
                            "id": "result_1",
                            "text_output": f"Origin={origin_ddm} | distance={distance} {unit} | bearing={bearing}°",
                            "confidence": 1.0,
                            "parameters": {
                                "mode": "detect",
                                "strict": True,
                                "distance": distance,
                                "distance_unit": unit,
                                "distance_m": dist_m,
                                "bearing_deg": bearing,
                            },
                            "metadata": {
                                "origin_ddm": origin_ddm,
                                "origin_decimal": {"latitude": origin_lat, "longitude": origin_lon},
                            },
                        }
                    ],
                )

            projection = self._extract_projection_info(text)
            if not projection["found"]:
                distance = self._safe_float(inputs.get("distance"), default=None)
                bearing = self._safe_float(inputs.get("bearing_deg"), default=None)
                unit = str(inputs.get("distance_unit", "m") or "m")

                if distance is None or bearing is None:
                    return self._error_response(start_time, "Aucune information de projection détectée dans le texte")

                dist_m = self._convert_distance_to_meters(distance, unit)
                summary = "Projection détectée (fallback champs)"
                return self._ok_response(
                    start_time,
                    summary,
                    [
                        {
                            "id": "result_1",
                            "text_output": f"Origin={origin_ddm} | distance={distance} {unit} | bearing={bearing}°",
                            "confidence": 1.0,
                            "parameters": {
                                "mode": "detect",
                                "strict": False,
                                "fallback_strict_inputs": True,
                                "distance": distance,
                                "distance_unit": unit,
                                "distance_m": dist_m,
                                "bearing_deg": bearing,
                            },
                            "metadata": {
                                "origin_ddm": origin_ddm,
                                "origin_decimal": {"latitude": origin_lat, "longitude": origin_lon},
                            },
                        }
                    ],
                )

            dist_m = self._convert_distance_to_meters(projection["distance"], projection["unit"])
            summary = "Projection détectée (mode smooth)"
            return self._ok_response(
                start_time,
                summary,
                [
                    {
                        "id": "result_1",
                        "text_output": (
                            f"Origin={origin_ddm} | distance={projection['distance']} {projection['unit']} "
                            f"| bearing={projection['angle']}°"
                        ),
                        "confidence": 1.0,
                        "parameters": {
                            "mode": "detect",
                            "strict": False,
                            "distance": projection["distance"],
                            "distance_unit": projection["unit"],
                            "distance_m": dist_m,
                            "bearing_deg": projection["angle"],
                        },
                        "metadata": {
                            "origin_ddm": origin_ddm,
                            "origin_decimal": {"latitude": origin_lat, "longitude": origin_lon},
                        },
                    }
                ],
            )

        if strict:
            distance = self._safe_float(inputs.get("distance"), default=None)
            bearing = self._safe_float(inputs.get("bearing_deg"), default=None)
            unit = str(inputs.get("distance_unit", "m") or "m")

            if distance is None or bearing is None:
                return self._error_response(start_time, "Distance et/ou azimut manquants en mode strict")

            dist_m = self._convert_distance_to_meters(distance, unit)
            log_message = self._format_log(
                strict=True,
                origin_ddm=origin_ddm,
                distance=distance,
                unit=unit,
                bearing=bearing,
                extracted=None,
            )

            new_lat, new_lon = self._project(origin_lat, origin_lon, dist_m, bearing)
            formatted = self._decimal_to_gc_coordinates(new_lat, new_lon)

            return self._build_projection_response(
                start_time,
                formatted=formatted,
                decimal=(new_lat, new_lon),
                log_message=log_message,
                enable_gps_detection=enable_gps_detection,
                parameters={
                    "mode": "decode",
                    "strict": True,
                    "origin": origin_ddm,
                    "distance": distance,
                    "distance_unit": unit,
                    "bearing_deg": bearing,
                },
            )

        projection = self._extract_projection_info(text)
        if not projection["found"]:
            distance = self._safe_float(inputs.get("distance"), default=None)
            bearing = self._safe_float(inputs.get("bearing_deg"), default=None)
            unit = str(inputs.get("distance_unit", "m") or "m")

            if distance is None or bearing is None:
                return self._error_response(start_time, "Aucune information de projection détectée dans le texte")

            dist_m = self._convert_distance_to_meters(distance, unit)
            log_message = (
                "Mode smooth: aucune information détectée dans le texte, utilisation des champs distance/azimut\n"
                f"Coordonnée d'origine: {origin_ddm}\n"
                f"Distance: {distance} {unit}\n"
                f"Azimut: {bearing}°"
            )

            new_lat, new_lon = self._project(origin_lat, origin_lon, dist_m, bearing)
            formatted = self._decimal_to_gc_coordinates(new_lat, new_lon)

            return self._build_projection_response(
                start_time,
                formatted=formatted,
                decimal=(new_lat, new_lon),
                log_message=log_message,
                enable_gps_detection=enable_gps_detection,
                parameters={
                    "mode": "decode",
                    "strict": False,
                    "fallback_strict_inputs": True,
                    "origin": origin_ddm,
                    "distance": distance,
                    "distance_unit": unit,
                    "bearing_deg": bearing,
                },
            )

        dist_m = self._convert_distance_to_meters(projection["distance"], projection["unit"])
        bearing = projection["angle"]

        log_message = self._format_log(
            strict=False,
            origin_ddm=origin_ddm,
            distance=projection["distance"],
            unit=projection["unit"],
            bearing=bearing,
            extracted=projection,
        )

        new_lat, new_lon = self._project(origin_lat, origin_lon, dist_m, bearing)
        formatted = self._decimal_to_gc_coordinates(new_lat, new_lon)

        return self._build_projection_response(
            start_time,
            formatted=formatted,
            decimal=(new_lat, new_lon),
            log_message=log_message,
            enable_gps_detection=enable_gps_detection,
            parameters={
                "mode": "decode",
                "strict": False,
                "origin": origin_ddm,
                "distance": projection["distance"],
                "distance_unit": projection["unit"],
                "bearing_deg": bearing,
            },
        )

    def _resolve_origin(
        self, origin_coords: str, text: str
    ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        if origin_coords.strip():
            return self._parse_origin_text(origin_coords)

        if not text.strip():
            return None, None, None

        return self._parse_origin_text(text)

    def _parse_origin_text(
        self, value: str
    ) -> Tuple[Optional[str], Optional[float], Optional[float]]:
        if detect_gps_coordinates is not None:
            detection = detect_gps_coordinates(value, include_numeric_only=False)
            if detection and detection.get("exist"):
                ddm = detection.get("ddm")
                lat = detection.get("decimal_latitude")
                lon = detection.get("decimal_longitude")

                if lat is None or lon is None:
                    ddm_lat = detection.get("ddm_lat")
                    ddm_lon = detection.get("ddm_lon")
                    if convert_ddm_to_decimal is not None and ddm_lat and ddm_lon:
                        dec = convert_ddm_to_decimal(ddm_lat, ddm_lon)
                        lat = dec.get("latitude")
                        lon = dec.get("longitude")

                try:
                    return str(ddm), float(lat), float(lon)
                except Exception:
                    return None, None, None

        lat, lon = self._parse_gc_ddm(value)
        if lat is None or lon is None:
            return None, None, None

        ddm = self._decimal_to_gc_coordinates(lat, lon)
        return ddm, lat, lon

    def _parse_gc_ddm(self, coord_str: str) -> Tuple[Optional[float], Optional[float]]:
        pattern = r"""\s*([NS])\s+(\d{1,2})\s*[°º]\s+(\d{1,2}(?:\.\d+)?)\s+([EW])\s+(\d{1,3})\s*[°º]\s+(\d{1,2}(?:\.\d+)?)\s*"""
        match = re.search(pattern, coord_str.strip(), re.IGNORECASE)
        if not match:
            return None, None

        ns, lat_deg_str, lat_min_str, ew, lon_deg_str, lon_min_str = match.groups()

        try:
            lat_deg = float(lat_deg_str)
            lat_min = float(lat_min_str)
            lon_deg = float(lon_deg_str)
            lon_min = float(lon_min_str)
        except Exception:
            return None, None

        lat = lat_deg + (lat_min / 60.0)
        lon = lon_deg + (lon_min / 60.0)
        if ns.upper() == "S":
            lat = -lat
        if ew.upper() == "W":
            lon = -lon

        return lat, lon

    def _project(self, lat: float, lon: float, distance_m: float, bearing_deg: float) -> Tuple[float, float]:
        # Destination point formula on a sphere.
        # https://www.movable-type.co.uk/scripts/latlong.html (destination point)
        lat1 = math.radians(float(lat))
        lon1 = math.radians(float(lon))
        brng = math.radians(float(bearing_deg) % 360.0)

        angular_distance = float(distance_m) / self.earth_radius_m

        sin_lat1 = math.sin(lat1)
        cos_lat1 = math.cos(lat1)
        sin_ad = math.sin(angular_distance)
        cos_ad = math.cos(angular_distance)

        lat2 = math.asin(sin_lat1 * cos_ad + cos_lat1 * sin_ad * math.cos(brng))

        y = math.sin(brng) * sin_ad * cos_lat1
        x = cos_ad - sin_lat1 * math.sin(lat2)
        lon2 = lon1 + math.atan2(y, x)

        # Normalize longitude to [-180, 180]
        lon2 = (lon2 + math.pi) % (2.0 * math.pi) - math.pi

        return float(math.degrees(lat2)), float(math.degrees(lon2))

    def _convert_distance_to_meters(self, distance: float, unit: str) -> float:
        unit = (unit or "m").lower().strip()

        if unit == "m":
            return float(distance)
        if unit == "km":
            return float(distance) * 1000.0
        if unit in {"mile", "miles"}:
            return float(distance) * 1609.344

        raise ValueError(f"Unité de distance inconnue: '{unit}'")

    def _decimal_to_gc_coordinates(self, lat: float, lon: float) -> str:
        lat_dir = "N" if lat >= 0 else "S"
        lon_dir = "E" if lon >= 0 else "W"

        lat_abs = abs(float(lat))
        lon_abs = abs(float(lon))

        lat_deg = int(lat_abs)
        lon_deg = int(lon_abs)

        lat_min = (lat_abs - lat_deg) * 60.0
        lon_min = (lon_abs - lon_deg) * 60.0

        return f"{lat_dir} {lat_deg}° {lat_min:.3f} {lon_dir} {lon_deg:03d}° {lon_min:.3f}"

    def _extract_projection_info(self, text: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {"found": False, "distance": None, "angle": None, "unit": "m"}
        if not text:
            return result

        normalized = text.replace(",", ".")

        unit_patterns = {
            "m": r"m(?:(?:è|e)tres?|eters?)?",
            "km": r"k(?:ilo)?(?:m(?:(?:è|e)tres?|eters?)?)",
            "miles": r"miles?",
        }
        all_units = "|".join(unit_patterns.values())
        degrees_pattern = r"(?:degr[ée]s|degrees?|°)"

        patterns = [
            r"(?:à|allez\sà|a|go|go\sto)\s+(\d+(?:\.\d+)?)\s*(" + all_units + r")?\s+(?:direction|azimut|cap|à|toward|towards|at|to)\s+(\d+(?:\.\d+)?)\s*(?:" + degrees_pattern + r")",
            r"(\d+(?:\.\d+)?)\s*(" + all_units + r")?\s+(\d+(?:\.\d+)?)\s*(?:" + degrees_pattern + r")",
            r"(\d+(?:\.\d+)?)(" + all_units + r")[\s,\.]+(\d+(?:\.\d+)?)\s*(?:" + degrees_pattern + r")",
            r"(?:projetez|projeter|project|move|go)(?:\s+(?:vous|yourself|from))?\s+(?:de|of|for)?\s+(\d+(?:\.\d+)?)\s*(" + all_units + r")(?:\s+(?:à|a|to|at|en direction))?\s+(\d+(?:\.\d+)?)\s*(?:" + degrees_pattern + r")",
            r"(?:à|a|at)?\s+(\d+(?:\.\d+)?)\s*(?:" + degrees_pattern + r")\s+(?:sur|sur une distance de|on|for|over)\s+(\d+(?:\.\d+)?)\s*(" + all_units + r")",
            r"project\s+yourself\s+(\d+(?:\.\d+)?)\s*(" + all_units + r")\s+at\s+(\d+(?:\.\d+)?)\s*(?:" + degrees_pattern + r")",
        ]

        for idx, pattern in enumerate(patterns):
            match = re.search(pattern, normalized, re.IGNORECASE)
            if not match:
                continue

            if idx == 4:
                angle_str, distance_str, unit = match.groups()
            else:
                distance_str, unit, angle_str = match.groups()

            try:
                distance = float(distance_str)
                angle = float(angle_str)
            except Exception:
                continue

            unit_key = "m"
            if unit:
                unit_l = unit.lower()
                for k, up in unit_patterns.items():
                    if re.match(r"^" + up + r"$", unit_l, re.IGNORECASE):
                        unit_key = k
                        break
                if "mile" in unit_l:
                    unit_key = "miles"

            result.update({"found": True, "distance": distance, "angle": angle, "unit": unit_key})
            return result

        return result

    def _build_projection_response(
        self,
        start_time: float,
        formatted: str,
        decimal: Tuple[float, float],
        log_message: str,
        enable_gps_detection: bool,
        parameters: Dict[str, Any],
    ) -> Dict[str, Any]:
        lat, lon = decimal

        result_item: Dict[str, Any] = {
            "id": "result_1",
            "text_output": formatted,
            "confidence": 1.0,
            "parameters": parameters,
            "metadata": {},
            "decimal_latitude": lat,
            "decimal_longitude": lon,
            "log": log_message,
        }

        if enable_gps_detection and detect_gps_coordinates is not None:
            detection = detect_gps_coordinates(formatted, include_numeric_only=False)
            if detection and detection.get("exist"):
                result_item["metadata"]["gps_coordinates"] = detection

        summary = "Projection calculée"
        return self._ok_response(start_time, summary, [result_item])

    def _format_log(
        self,
        strict: bool,
        origin_ddm: str,
        distance: float,
        unit: str,
        bearing: float,
        extracted: Optional[Dict[str, Any]],
    ) -> str:
        if strict:
            return (
                "Mode strict: projection directe\n"
                f"Coordonnée d'origine: {origin_ddm}\n"
                f"Distance: {distance} {unit}\n"
                f"Azimut: {bearing}°"
            )

        if extracted and extracted.get("found"):
            return (
                "Mode smooth: extraction depuis le texte\n"
                f"Coordonnée d'origine: {origin_ddm}\n"
                f"Distance extraite: {extracted.get('distance')} {extracted.get('unit')}\n"
                f"Azimut extrait: {extracted.get('angle')}°"
            )

        return "Mode smooth: aucune information extraite"

    def _safe_float(self, value: Any, default: Optional[float]) -> Optional[float]:
        if value is None:
            return default

        if isinstance(value, (int, float)):
            return float(value)

        try:
            raw = str(value).strip()
        except Exception:
            return default

        if not raw:
            return default

        raw = raw.replace(",", ".")

        match = re.search(r"[-+]?\d+(?:\.\d+)?", raw)
        if not match:
            return default

        try:
            return float(match.group(0))
        except Exception:
            return default

    def _ok_response(self, start_time: float, summary: str, results: list[Dict[str, Any]]) -> Dict[str, Any]:
        execution_time = int((time.time() - start_time) * 1000)
        return {
            "status": "ok",
            "summary": summary,
            "results": results,
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": execution_time,
            },
        }

    def _error_response(self, start_time: float, message: str) -> Dict[str, Any]:
        execution_time = int((time.time() - start_time) * 1000)
        return {
            "status": "error",
            "summary": message,
            "results": [],
            "plugin_info": {
                "name": self.name,
                "version": self.version,
                "execution_time_ms": execution_time,
            },
        }


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Plugin entry point for the PluginManager."""
    plugin = CoordinateProjectionPlugin()
    return plugin.execute(inputs)
