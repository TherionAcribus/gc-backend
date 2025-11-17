from __future__ import annotations

from datetime import datetime, timezone

from ..database import db


class Geocache(db.Model):
    __tablename__ = 'geocache'

    id = db.Column(db.Integer, primary_key=True)
    gc_code = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(500))
    type = db.Column(db.String(100))
    size = db.Column(db.String(50))
    owner = db.Column(db.String(255))
    difficulty = db.Column(db.Float)
    terrain = db.Column(db.Float)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    placed_at = db.Column(db.DateTime)
    status = db.Column(db.String(50), default='active')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Données enrichies (scraping)
    coordinates_raw = db.Column(db.String(100))  # Coordonnées affichées au format Geocaching (peuvent être corrigées)
    is_corrected = db.Column(db.Boolean)
    original_latitude = db.Column(db.Float)  # Coordonnées originales en décimal (pour la carte)
    original_longitude = db.Column(db.Float)  # Coordonnées originales en décimal (pour la carte)
    original_coordinates_raw = db.Column(db.String(100))  # Coordonnées originales au format Geocaching (format utilisé par les joueurs)
    description_html = db.Column(db.Text)
    description_raw = db.Column(db.Text)
    hints = db.Column(db.Text)
    attributes = db.Column(db.JSON)
    favorites_count = db.Column(db.Integer)
    logs_count = db.Column(db.Integer)
    images = db.Column(db.JSON)  # liste d'objets {url: str}
    found = db.Column(db.Boolean)
    found_date = db.Column(db.DateTime)
    solved = db.Column(db.String(20), default='not_solved')  # not_solved, in_progress, solved

    zone_id = db.Column(db.Integer, db.ForeignKey('zone.id'), nullable=False)
    zone = db.relationship('Zone', backref=db.backref('geocaches', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('gc_code', 'zone_id', name='unique_gc_code_zone'),
    )

    waypoints = db.relationship('GeocacheWaypoint', back_populates='geocache', cascade='all, delete-orphan', lazy=True)
    checkers = db.relationship('GeocacheChecker', back_populates='geocache', cascade='all, delete-orphan', lazy=True)

    def to_list_item(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'gc_code': self.gc_code,
        }

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'gc_code': self.gc_code,
            'name': self.name,
            'url': self.url,
            'type': self.type,
            'size': self.size,
            'owner': self.owner,
            'difficulty': self.difficulty,
            'terrain': self.terrain,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'placed_at': self.placed_at.isoformat() if self.placed_at else None,
            'status': self.status,
            'zone_id': self.zone_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'coordinates_raw': self.coordinates_raw,
            'is_corrected': self.is_corrected,
            'original_latitude': self.original_latitude,
            'original_longitude': self.original_longitude,
            'original_coordinates_raw': self.original_coordinates_raw,
            'description_html': self.description_html,
            'description_raw': self.description_raw,
            'hints': self.hints,
            'attributes': self.attributes,
            'favorites_count': self.favorites_count,
            'logs_count': self.logs_count,
            'images': self.images,
            'found': self.found,
            'found_date': self.found_date.isoformat() if self.found_date else None,
            'solved': self.solved,
            'waypoints': [w.to_dict() for w in (self.waypoints or [])],
            'checkers': [c.to_dict() for c in (self.checkers or [])],
        }


class GeocacheWaypoint(db.Model):
    __tablename__ = 'geocache_waypoint'

    id = db.Column(db.Integer, primary_key=True)
    geocache_id = db.Column(db.Integer, db.ForeignKey('geocache.id'), nullable=False, index=True)
    prefix = db.Column(db.String(20))
    lookup = db.Column(db.String(50))
    name = db.Column(db.String(255))
    type = db.Column(db.String(100))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    gc_coords = db.Column(db.String(100))
    note = db.Column(db.Text)

    geocache = db.relationship('Geocache', back_populates='waypoints')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'prefix': self.prefix,
            'lookup': self.lookup,
            'name': self.name,
            'type': self.type,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'gc_coords': self.gc_coords,
            'note': self.note,
        }


class GeocacheChecker(db.Model):
    __tablename__ = 'geocache_checker'

    id = db.Column(db.Integer, primary_key=True)
    geocache_id = db.Column(db.Integer, db.ForeignKey('geocache.id'), nullable=False, index=True)
    name = db.Column(db.String(100))
    url = db.Column(db.String(1000))

    geocache = db.relationship('Geocache', back_populates='checkers')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'url': self.url,
        }


