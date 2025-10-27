from __future__ import annotations

from datetime import datetime, timezone

from ..database import db


class Geocache(db.Model):
    __tablename__ = 'geocache'

    id = db.Column(db.Integer, primary_key=True)
    gc_code = db.Column(db.String(20), nullable=False, unique=True, index=True)
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

    zone_id = db.Column(db.Integer, db.ForeignKey('zone.id'), nullable=False)
    zone = db.relationship('Zone', backref=db.backref('geocaches', lazy=True))

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
        }


