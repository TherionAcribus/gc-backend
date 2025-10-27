from datetime import datetime, timezone

from .database import db


class Zone(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # Relation many-to-many avec Geocache (à implémenter plus tard)
    # geocaches = db.relationship('Geocache', secondary='geocache_zone', back_populates='zones')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'geocaches_count': len(self.geocaches) if hasattr(self, 'geocaches') and self.geocaches else 0,
        }


class AppConfig(db.Model):
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text)

    @staticmethod
    def get_value(key: str, default: str | None = None) -> str | None:
        entry = AppConfig.query.get(key)
        return entry.value if entry is not None else default

    @staticmethod
    def set_value(key: str, value: str | None) -> None:
        entry = AppConfig.query.get(key)
        if entry is None:
            entry = AppConfig(key=key, value=value)
            db.session.add(entry)
        else:
            entry.value = value


