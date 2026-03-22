from __future__ import annotations

import codecs
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
    description_override_html = db.Column(db.Text)
    description_override_raw = db.Column(db.Text)
    description_override_updated_at = db.Column(db.DateTime)
    hints = db.Column(db.Text)
    hints_decoded = db.Column(db.Text)
    hints_decoded_override = db.Column(db.Text)
    hints_decoded_override_updated_at = db.Column(db.DateTime)
    attributes = db.Column(db.JSON)
    favorites_count = db.Column(db.Integer)
    logs_count = db.Column(db.Integer)
    images = db.Column(db.JSON)  # liste d'objets {url: str}
    found = db.Column(db.Boolean)
    found_date = db.Column(db.DateTime)
    solved = db.Column(db.String(20), default='not_solved')  # not_solved, in_progress, solved

    gc_personal_note = db.Column(db.Text)
    gc_personal_note_synced_at = db.Column(db.DateTime)
    gc_personal_note_last_pushed_at = db.Column(db.DateTime)

    zone_id = db.Column(db.Integer, db.ForeignKey('zone.id'), nullable=False)
    zone = db.relationship('Zone', backref=db.backref('geocaches', lazy=True))

    __table_args__ = (
        db.UniqueConstraint('gc_code', 'zone_id', name='unique_gc_code_zone'),
    )

    waypoints = db.relationship('GeocacheWaypoint', back_populates='geocache', cascade='all, delete-orphan', lazy=True)
    checkers = db.relationship('GeocacheChecker', back_populates='geocache', cascade='all, delete-orphan', lazy=True)
    logs = db.relationship('GeocacheLog', back_populates='geocache', cascade='all, delete-orphan', lazy=True, order_by='desc(GeocacheLog.date)')
    notes = db.relationship('Note', secondary='geocache_note', back_populates='geocaches', lazy=True)
    images_v2 = db.relationship('GeocacheImage', back_populates='geocache', cascade='all, delete-orphan', lazy=True)

    def to_list_item(self) -> dict:
        return {
            'id': self.id,
            'name': self.name,
            'gc_code': self.gc_code,
        }

    @staticmethod
    def decode_hint_rot13(value: str) -> str:
        return codecs.decode(value, 'rot_13')

    def to_dict(self) -> dict:
        decoded_hints = self.hints_decoded
        if decoded_hints is None and self.hints:
            decoded_hints = self.decode_hint_rot13(self.hints)
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
            'description_override_html': self.description_override_html,
            'description_override_raw': self.description_override_raw,
            'description_override_updated_at': self.description_override_updated_at.isoformat() if self.description_override_updated_at else None,
            'hints': self.hints,
            'hints_decoded': decoded_hints,
            'hints_decoded_override': self.hints_decoded_override,
            'hints_decoded_override_updated_at': self.hints_decoded_override_updated_at.isoformat() if self.hints_decoded_override_updated_at else None,
            'attributes': self.attributes,
            'favorites_count': self.favorites_count,
            'logs_count': self.logs_count,
            'images': self.images,
            'found': self.found,
            'found_date': self.found_date.isoformat() if self.found_date else None,
            'solved': self.solved,
            'waypoints': [w.to_dict() for w in (self.waypoints or [])],
            'checkers': [c.to_dict() for c in (self.checkers or [])],
            'gc_personal_note': self.gc_personal_note,
            'gc_personal_note_synced_at': self.gc_personal_note_synced_at.isoformat() if self.gc_personal_note_synced_at else None,
            'gc_personal_note_last_pushed_at': self.gc_personal_note_last_pushed_at.isoformat() if self.gc_personal_note_last_pushed_at else None,
        }


class GeocacheImage(db.Model):
    __tablename__ = 'geocache_image'

    id = db.Column(db.Integer, primary_key=True)
    geocache_id = db.Column(db.Integer, db.ForeignKey('geocache.id'), nullable=False, index=True)
    source_url = db.Column(db.String(2000), nullable=False)

    stored = db.Column(db.Boolean, default=False)
    stored_path = db.Column(db.String(1000))
    mime_type = db.Column(db.String(100))
    byte_size = db.Column(db.Integer)
    sha256 = db.Column(db.String(64))

    parent_image_id = db.Column(db.Integer, db.ForeignKey('geocache_image.id'))
    derivation_type = db.Column(db.String(20), default='original')
    crop_rect = db.Column(db.JSON)

    editor_state_json = db.Column(db.Text)

    title = db.Column(db.String(255))
    note = db.Column(db.Text)
    tags = db.Column(db.JSON)
    detected_features = db.Column(db.JSON)
    qr_payload = db.Column(db.Text)
    ocr_text = db.Column(db.Text)
    ocr_language = db.Column(db.String(20))

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    geocache = db.relationship('Geocache', back_populates='images_v2')
    parent_image = db.relationship('GeocacheImage', remote_side=[id])

    __table_args__ = (
        db.UniqueConstraint('geocache_id', 'source_url', 'parent_image_id', 'derivation_type', name='unique_geocache_image_variant'),
    )

    def get_display_url(self) -> str:
        if self.stored:
            return f'/api/geocache-images/{self.id}/content'
        return self.source_url

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'geocache_id': self.geocache_id,
            'url': self.get_display_url(),
            'source_url': self.source_url,
            'stored': bool(self.stored),
            'stored_path': self.stored_path,
            'mime_type': self.mime_type,
            'byte_size': self.byte_size,
            'sha256': self.sha256,
            'parent_image_id': self.parent_image_id,
            'derivation_type': self.derivation_type,
            'crop_rect': self.crop_rect,
            'title': self.title,
            'note': self.note,
            'tags': self.tags,
            'detected_features': self.detected_features,
            'qr_payload': self.qr_payload,
            'ocr_text': self.ocr_text,
            'ocr_language': self.ocr_language,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
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
    note_override = db.Column(db.Text)
    note_override_updated_at = db.Column(db.DateTime)

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
            'note_override': self.note_override,
            'note_override_updated_at': self.note_override_updated_at.isoformat() if self.note_override_updated_at else None,
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


class GeocacheLog(db.Model):
    """
    Modèle représentant un log (commentaire) laissé sur une géocache.
    Les logs sont récupérés depuis Geocaching.com et stockés localement.
    """
    __tablename__ = 'geocache_log'

    id = db.Column(db.Integer, primary_key=True)
    geocache_id = db.Column(db.Integer, db.ForeignKey('geocache.id'), nullable=False, index=True)
    
    # ID externe du log sur Geocaching.com (pour éviter les doublons)
    external_id = db.Column(db.String(50), index=True)
    
    # Auteur du log
    author = db.Column(db.String(255))
    author_guid = db.Column(db.String(100))  # GUID de l'auteur sur Geocaching.com
    
    # Contenu du log
    text = db.Column(db.Text)
    date = db.Column(db.DateTime, index=True)
    
    # Type de log normalisé : Found, Did Not Find, Note, Owner Maintenance, etc.
    log_type = db.Column(db.String(50), index=True)
    
    # Log marqué comme favori par l'auteur
    is_favorite = db.Column(db.Boolean, default=False)
    
    # Métadonnées
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    geocache = db.relationship('Geocache', back_populates='logs')

    __table_args__ = (
        db.UniqueConstraint('geocache_id', 'external_id', name='unique_log_per_geocache'),
    )

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'external_id': self.external_id,
            'author': self.author,
            'author_guid': self.author_guid,
            'text': self.text,
            'date': self.date.isoformat() if self.date else None,
            'log_type': self.log_type,
            'is_favorite': self.is_favorite,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }

    @staticmethod
    def normalize_log_type(log_type: str | None) -> str:
        """
        Normalise le type de log pour avoir une cohérence dans la base de données.
        
        Args:
            log_type: Type de log brut depuis l'API Geocaching
            
        Returns:
            Type de log normalisé
        """
        if not log_type:
            return 'Other'
        
        log_type_lower = log_type.lower().strip()
        
        # Mapping des types de logs courants
        type_mapping = {
            'found it': 'Found',
            'found': 'Found',
            "didn't find it": 'Did Not Find',
            'did not find': 'Did Not Find',
            'dnf': 'Did Not Find',
            'write note': 'Note',
            'note': 'Note',
            'webcam photo taken': 'Webcam',
            'webcam': 'Webcam',
            'owner maintenance': 'Owner Maintenance',
            'maintenance': 'Owner Maintenance',
            'needs maintenance': 'Needs Maintenance',
            'needs archived': 'Needs Archived',
            'will attend': 'Will Attend',
            'attended': 'Attended',
            'temporarily disable listing': 'Temporarily Disabled',
            'enable listing': 'Enabled',
            'publish listing': 'Published',
            'retract listing': 'Retracted',
            'archive': 'Archived',
            'unarchive': 'Unarchived',
            'reviewer note': 'Reviewer Note',
            'post reviewer note': 'Reviewer Note',
        }
        
        if log_type_lower in type_mapping:
            return type_mapping[log_type_lower]
        
        # Par défaut, capitaliser la première lettre
        return log_type.strip().title()


class Note(db.Model):
    __tablename__ = 'note'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    note_type = db.Column(db.String(50), nullable=False)
    source = db.Column(db.String(50), nullable=False, default='user')
    source_plugin = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    geocaches = db.relationship('Geocache', secondary='geocache_note', back_populates='notes')

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'content': self.content,
            'note_type': self.note_type,
            'source': self.source,
            'source_plugin': self.source_plugin,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class GeocacheNote(db.Model):
    __tablename__ = 'geocache_note'

    geocache_id = db.Column(db.Integer, db.ForeignKey('geocache.id'), primary_key=True)
    note_id = db.Column(db.Integer, db.ForeignKey('note.id'), primary_key=True)
    added_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class SolvedGeocacheArchive(db.Model):
    """
    Table d'archive des données de résolution des géocaches.
    Persiste indépendamment de la table geocache (pas de FK).
    Survit à la suppression de la géocache.
    """
    __tablename__ = 'solved_geocache_archive'

    id = db.Column(db.Integer, primary_key=True)
    gc_code = db.Column(db.String(20), nullable=False, unique=True, index=True)

    # Informations de base (contexte)
    name = db.Column(db.String(255))
    cache_type = db.Column(db.String(100))
    difficulty = db.Column(db.Float)
    terrain = db.Column(db.Float)

    # Résolution
    solved_status = db.Column(db.String(20), default='not_solved')  # not_solved, in_progress, solved
    solved_coordinates_raw = db.Column(db.String(100))
    solved_latitude = db.Column(db.Float)
    solved_longitude = db.Column(db.Float)
    original_coordinates_raw = db.Column(db.String(100))

    # Données de travail (snapshots)
    notes_snapshot = db.Column(db.Text)         # JSON list of {content, note_type, source, source_plugin}
    personal_note = db.Column(db.Text)
    formula_data = db.Column(db.Text)           # JSON {variables: {A:1,...}, formula: '...', result: '...'}
    waypoints_snapshot = db.Column(db.Text)     # JSON list of waypoints

    # Trouvée physiquement
    found = db.Column(db.Boolean)
    found_date = db.Column(db.DateTime)

    # Traçabilité de la résolution
    resolution_method = db.Column(db.String(50))   # manual, formula, plugin, brute_force
    resolution_plugins = db.Column(db.Text)         # JSON list of plugin names
    resolution_diagnostics = db.Column(db.Text)     # JSON snapshot from assistant/plugin executor workflows

    # Métadonnées
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        import json as _json
        def _load(val):
            if val is None:
                return None
            try:
                return _json.loads(val)
            except Exception:
                return val

        return {
            'id': self.id,
            'gc_code': self.gc_code,
            'name': self.name,
            'cache_type': self.cache_type,
            'difficulty': self.difficulty,
            'terrain': self.terrain,
            'solved_status': self.solved_status,
            'solved_coordinates_raw': self.solved_coordinates_raw,
            'solved_latitude': self.solved_latitude,
            'solved_longitude': self.solved_longitude,
            'original_coordinates_raw': self.original_coordinates_raw,
            'notes_snapshot': _load(self.notes_snapshot),
            'personal_note': self.personal_note,
            'formula_data': _load(self.formula_data),
            'waypoints_snapshot': _load(self.waypoints_snapshot),
            'found': self.found,
            'found_date': self.found_date.isoformat() if self.found_date else None,
            'resolution_method': self.resolution_method,
            'resolution_plugins': _load(self.resolution_plugins),
            'resolution_diagnostics': _load(self.resolution_diagnostics),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
