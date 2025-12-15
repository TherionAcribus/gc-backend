"""Utilities for locating GeoApp Playwright storage directories."""

from __future__ import annotations

import os
from pathlib import Path


def get_default_profile_dir() -> Path:
    """Returns the default Playwright profile directory for GeoApp."""
    base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
    if base:
        return Path(base) / 'GeoApp' / 'playwright-profile'
    return Path.home() / '.geoapp' / 'playwright-profile'
