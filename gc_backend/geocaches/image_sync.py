"""Synchronization helpers to keep legacy Geocache.images JSON and GeocacheImage rows aligned."""

from __future__ import annotations

import logging
from typing import Iterable

from ..database import db
from .models import Geocache, GeocacheImage


logger = logging.getLogger(__name__)


def extract_image_urls(images_json: object) -> list[str]:
    """Extract a list of URLs from the legacy Geocache.images JSON payload."""
    if not images_json:
        return []

    if isinstance(images_json, list):
        urls: list[str] = []
        for entry in images_json:
            if isinstance(entry, dict):
                url = (entry.get('url') or '').strip()
                if url:
                    urls.append(url)
        return urls

    return []


def ensure_images_v2_for_geocache(geocache: Geocache) -> int:
    """Ensure GeocacheImage rows exist for the geocache legacy images.

    Returns:
        Number of images created.
    """
    urls = extract_image_urls(geocache.images)
    if not urls:
        return 0

    created = 0
    for url in urls:
        existing = GeocacheImage.query.filter_by(
            geocache_id=geocache.id,
            source_url=url,
            parent_image_id=None,
            derivation_type='original',
        ).first()
        if existing:
            continue

        db.session.add(
            GeocacheImage(
                geocache_id=geocache.id,
                source_url=url,
                derivation_type='original',
            )
        )
        created += 1

    return created


def ensure_images_v2_for_all_geocaches(geocaches: Iterable[Geocache] | None = None) -> int:
    """Backfill GeocacheImage rows from all geocaches legacy images."""
    query = geocaches if geocaches is not None else Geocache.query.all()
    created_total = 0
    for geocache in query:
        created_total += ensure_images_v2_for_geocache(geocache)

    if created_total:
        db.session.commit()
        logger.info('Backfilled %s geocache images (v2)', created_total)

    return created_total
