import logging
from flask import Flask
from flask_cors import CORS

from .config import Config
from .database import init_db


# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    # CORS pour l'application Theia (browser 3000)
    CORS(
        app,
        supports_credentials=True,
        resources={r"/*": {"origins": ["http://127.0.0.1:3000", "http://localhost:3000", "*"]}},
    )

    # Init DB et données par défaut
    init_db(app)

    # Blueprints
    from .blueprints.zones import bp as zones_bp
    from .blueprints.geocaches import bp as geocaches_bp

    app.register_blueprint(zones_bp)
    app.register_blueprint(geocaches_bp)

    return app


