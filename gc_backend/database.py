import logging
from flask_sqlalchemy import SQLAlchemy

logger = logging.getLogger(__name__)

db = SQLAlchemy()


def init_db(app):
    db.init_app(app)

    with app.app_context():
        from .models import Zone  # noqa
        # Importer le modèle Geocache pour la création de table
        from .geocaches.models import Geocache  # noqa: F401

        logger.info("Creating database tables if not exist…")
        db.create_all()

        # Zone par défaut
        try:
            default_zone = Zone.query.filter_by(name="default").first()
            if default_zone is None:
                default_zone = Zone(name="default", description="Default zone")
                db.session.add(default_zone)
                db.session.commit()
            else:
                logger.info("Default zone already exists")
        except Exception as e:
            logger.error(f"Error creating default zone: {e}")
            db.session.rollback()


