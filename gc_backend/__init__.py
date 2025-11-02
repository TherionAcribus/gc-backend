import logging
from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate

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

    # Init Flask-Migrate
    from .database import db
    migrate = Migrate(app, db)

    # Blueprints
    from .blueprints.zones import bp as zones_bp
    from .blueprints.geocaches import bp as geocaches_bp
    from .blueprints.plugins import bp as plugins_bp, init_plugin_manager
    from .blueprints.tasks import bp as tasks_bp, init_task_manager

    app.register_blueprint(zones_bp)
    app.register_blueprint(geocaches_bp)
    app.register_blueprint(plugins_bp)
    app.register_blueprint(tasks_bp)

    # Initialiser le PluginManager
    from .plugins import PluginManager
    import os
    
    plugins_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'plugins')
    plugin_manager = PluginManager(plugins_dir, app)
    
    # Découvrir les plugins au démarrage (SAUF pendant les migrations Alembic ou les tests)
    # Pendant "flask db upgrade", on skip la découverte pour éviter l'erreur "no such table"
    # Pendant les tests (TESTING=1), on skip pour éviter les conflits avec les fixtures
    import sys
    is_migration = 'flask' in sys.argv[0] and 'db' in sys.argv
    is_testing = os.environ.get('TESTING') == '1'
    
    if not is_migration and not is_testing:
        with app.app_context():
            plugin_manager.discover_plugins()
    
    # Initialiser le blueprint plugins avec le manager
    init_plugin_manager(plugin_manager)
    
    # Stocker le manager dans l'app pour accès global
    app.plugin_manager = plugin_manager
    
    # Initialiser le TaskManager
    from .services import TaskManager
    
    task_manager = TaskManager(max_workers=4)
    
    # Initialiser le blueprint tasks avec les managers
    init_task_manager(task_manager, plugin_manager)
    
    # Stocker le task manager dans l'app
    app.task_manager = task_manager

    return app


