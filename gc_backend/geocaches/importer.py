from __future__ import annotations

import logging
from typing import Optional

from ..database import db
from ..models import Zone
from .models import Geocache, GeocacheWaypoint, GeocacheChecker
from .scraper import GeocachingScraper
from .image_sync import ensure_images_v2_for_geocache
from .archive_service import ArchiveService


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
        # Données enrichies
        g.coordinates_raw = getattr(s, 'coordinates_raw', None)
        g.is_corrected = getattr(s, 'is_corrected', None)
        g.original_latitude = getattr(s, 'original_latitude', None)
        g.original_longitude = getattr(s, 'original_longitude', None)
        g.original_coordinates_raw = getattr(s, 'original_coordinates_raw', None)
        g.description_html = getattr(s, 'description_html', None)
        g.description_raw = getattr(s, 'description_raw', None)
        g.hints = getattr(s, 'hints', None)
        g.hints_decoded = Geocache.decode_hint_rot13(g.hints) if g.hints else None
        g.attributes = getattr(s, 'attributes', None)
        g.favorites_count = getattr(s, 'favorites_count', None)
        g.logs_count = getattr(s, 'logs_count', None)
        g.images = getattr(s, 'images', None)
        g.found = getattr(s, 'found', None)
        g.found_date = getattr(s, 'found_date', None)

        try:
            db.session.add(g)
            db.session.flush()

            ensure_images_v2_for_geocache(g)

            # Restaurer les données depuis l'archive si elle existe
            archive = ArchiveService.get_by_gc_code(code)
            if archive:
                logger.info(f"Archive found for {code}, restoring resolution data")
                if archive.get('solved_status') and archive['solved_status'] != 'not_solved':
                    g.solved = archive['solved_status']
                if archive.get('solved_coordinates_raw'):
                    g.coordinates_raw = archive['solved_coordinates_raw']
                    g.latitude = archive.get('solved_latitude')
                    g.longitude = archive.get('solved_longitude')
                    g.is_corrected = True
                if archive.get('personal_note') and not g.gc_personal_note:
                    g.gc_personal_note = archive['personal_note']
                if archive.get('found') and not g.found:
                    g.found = archive['found']
                    g.found_date = None  # found_date will be set from archive JSON if needed

            # Persistance des relations si disponibles
            for w in getattr(s, 'waypoints', []) or []:
                db.session.add(GeocacheWaypoint(
                    geocache_id=g.id,
                    prefix=w.get('prefix'),
                    lookup=w.get('lookup'),
                    name=w.get('name'),
                    type=w.get('type'),
                    latitude=w.get('latitude'),
                    longitude=w.get('longitude'),
                    gc_coords=w.get('gc_coords'),
                    note=w.get('note'),
                ))
            for c in getattr(s, 'checkers', []) or []:
                db.session.add(GeocacheChecker(
                    geocache_id=g.id,
                    name=c.get('name'),
                    url=c.get('url'),
                ))

            db.session.commit()
            logger.info(f"Geocache {code} imported successfully (id={g.id})")
            return g
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save geocache {code}: {e}")
            raise


