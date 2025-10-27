from __future__ import annotations

import logging
from typing import Optional

from ..database import db
from ..models import Zone
from .models import Geocache
from .scraper import GeocachingScraper


logger = logging.getLogger(__name__)


class GeocacheImporter:
    def __init__(self, scraper: Optional[GeocachingScraper] = None) -> None:
        self.scraper = scraper or GeocachingScraper()

    def import_by_code(self, zone_id: int, gc_code: str) -> Geocache:
        logger.info(f"Importing geocache {gc_code} into zone {zone_id}")

        if not isinstance(zone_id, int):
            logger.error(f"Invalid zone_id type: {type(zone_id)}")
            raise ValueError('invalid_zone_id')

        # Vérifier zone existante
        zone = Zone.query.get(zone_id)
        if zone is None:
            logger.warning(f"Zone {zone_id} not found")
            raise LookupError('zone_not_found')

        logger.debug(f"Zone {zone_id} exists: {zone.name}")

        # Normaliser/valider le code et vérifier déduplication
        code = self.scraper.validate_gc_code(gc_code)
        logger.debug(f"Validated GC code: {code}")

        existing = Geocache.query.filter_by(gc_code=code).first()
        if existing:
            logger.info(f"Geocache {code} already exists (id={existing.id})")
            # Idempotent: si déjà liée à cette zone, retourner tel quel
            if existing.zone_id == zone_id:
                logger.info(f"Geocache {code} already in zone {zone_id}")
                return existing
            # Sinon, pour ce MVP: réassocier à la nouvelle zone (simple)
            logger.info(f"Moving geocache {code} from zone {existing.zone_id} to {zone_id}")
            existing.zone_id = zone_id
            db.session.commit()
            logger.info(f"Geocache {code} moved successfully")
            return existing

        logger.info(f"Geocache {code} not found locally, scraping...")

        # Scraper
        try:
            s = self.scraper.scrape(code)
        except Exception as e:
            logger.error(f"Failed to scrape geocache {code}: {e}")
            raise

        logger.info(f"Creating geocache {code} in database")

        g = Geocache(
            gc_code=s.gc_code,
            name=s.name,
            url=s.url,
            type=s.type,
            size=s.size,
            owner=s.owner,
            difficulty=s.difficulty,
            terrain=s.terrain,
            latitude=s.latitude,
            longitude=s.longitude,
            placed_at=s.placed_at,
            status=s.status or 'active',
            zone_id=zone_id,
        )

        try:
            db.session.add(g)
            db.session.commit()
            logger.info(f"Geocache {code} imported successfully (id={g.id})")
            return g
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save geocache {code}: {e}")
            raise


