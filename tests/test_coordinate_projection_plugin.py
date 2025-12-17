"""Tests for the official plugin: coordinate_projection."""

from pathlib import Path

from gc_backend.plugins.wrappers import PluginMetadata, PluginType, PythonPluginWrapper


def test_coordinate_projection_strict_mode_returns_coordinates():
    """The plugin should project a point in strict mode and return decimal coordinates."""

    plugin_dir = (
        Path(__file__).parent.parent
        / "plugins"
        / "official"
        / "coordinate_projection"
    )

    metadata = PluginMetadata(
        name="coordinate_projection",
        version="1.0.0",
        plugin_type=PluginType.PYTHON,
        entry_point="main.py",
        path=str(plugin_dir),
        timeout_seconds=30,
    )

    wrapper = PythonPluginWrapper(metadata)
    assert wrapper.initialize() is True

    result = wrapper.execute(
        {
            "mode": "decode",
            "strict": "strict",
            "origin_coords": "N 48° 51.400 E 002° 21.050",
            "distance": 1000,
            "distance_unit": "m",
            "bearing_deg": 90,
            "enable_gps_detection": False,
        }
    )

    assert result["status"] == "ok"
    assert result["results"]

    first = result["results"][0]
    assert isinstance(first.get("text_output"), str)
    assert first.get("decimal_latitude") is not None
    assert first.get("decimal_longitude") is not None

    # East projection should increase longitude.
    assert first["decimal_longitude"] > 2.35


def test_coordinate_projection_smooth_mode_extracts_distance_and_bearing():
    """The plugin should extract projection info from text in smooth mode."""

    plugin_dir = (
        Path(__file__).parent.parent
        / "plugins"
        / "official"
        / "coordinate_projection"
    )

    metadata = PluginMetadata(
        name="coordinate_projection",
        version="1.0.0",
        plugin_type=PluginType.PYTHON,
        entry_point="main.py",
        path=str(plugin_dir),
        timeout_seconds=30,
    )

    wrapper = PythonPluginWrapper(metadata)
    assert wrapper.initialize() is True

    text = "N 48° 51.400 E 002° 21.050 puis projetez vous de 1 km à 90 degrés"

    result = wrapper.execute(
        {
            "mode": "decode",
            "strict": "smooth",
            "text": text,
            "enable_gps_detection": False,
        }
    )

    assert result["status"] == "ok"
    assert result["results"]

    first = result["results"][0]
    assert first.get("decimal_latitude") is not None
    assert first.get("decimal_longitude") is not None
    assert first["decimal_longitude"] > 2.35


def test_coordinate_projection_smooth_mode_fallback_accepts_degree_symbol_inputs():
    """Fallback should accept decorated numeric strings like '43.30°' sent by the UI."""

    plugin_dir = (
        Path(__file__).parent.parent
        / "plugins"
        / "official"
        / "coordinate_projection"
    )

    metadata = PluginMetadata(
        name="coordinate_projection",
        version="1.0.0",
        plugin_type=PluginType.PYTHON,
        entry_point="main.py",
        path=str(plugin_dir),
        timeout_seconds=30,
    )

    wrapper = PythonPluginWrapper(metadata)
    assert wrapper.initialize() is True

    result = wrapper.execute(
        {
            "mode": "decode",
            "strict": "smooth",
            "text": "",
            "origin_coords": "N 47° 53.188 E 007° 02.235",
            "distance": "2628",
            "distance_unit": "m",
            "bearing_deg": "43.30°",
            "enable_gps_detection": False,
        }
    )

    assert result["status"] == "ok"
    assert result["results"]


def test_coordinate_projection_smooth_mode_fallbacks_to_strict_inputs_when_no_text():
    """Smooth mode should fallback to distance/bearing inputs if text has no projection info."""

    plugin_dir = (
        Path(__file__).parent.parent
        / "plugins"
        / "official"
        / "coordinate_projection"
    )

    metadata = PluginMetadata(
        name="coordinate_projection",
        version="1.0.0",
        plugin_type=PluginType.PYTHON,
        entry_point="main.py",
        path=str(plugin_dir),
        timeout_seconds=30,
    )

    wrapper = PythonPluginWrapper(metadata)
    assert wrapper.initialize() is True

    result = wrapper.execute(
        {
            "mode": "decode",
            "strict": "smooth",
            "text": "",
            "origin_coords": "N 48° 51.400 E 002° 21.050",
            "distance": 1000,
            "distance_unit": "m",
            "bearing_deg": 90,
            "enable_gps_detection": False,
        }
    )

    assert result["status"] == "ok"
    assert result["results"]

    first = result["results"][0]
    assert first.get("decimal_latitude") is not None
    assert first.get("decimal_longitude") is not None
    assert first["decimal_longitude"] > 2.35
