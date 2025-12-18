import logging
from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate

from .config import Config
from .database import init_db
from .utils.preferences import get_value_or_default


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

    with app.app_context():
        auto_discover_plugins = bool(get_value_or_default('geoApp.plugins.autoDiscoverOnStart', True))
        task_auto_start = bool(get_value_or_default('geoApp.tasks.autoStartBackground', True))
        task_max_workers = int(get_value_or_default('geoApp.tasks.maxWorkers', 4))

    # Init Flask-Migrate
    from .database import db
    migrate = Migrate(app, db)

    # Blueprints
    from .blueprints.zones import bp as zones_bp
    from .blueprints.geocaches import bp as geocaches_bp
    from .blueprints.plugins import bp as plugins_bp, init_plugin_manager
    from .blueprints.tasks import bp as tasks_bp, init_task_manager
    from .blueprints.coordinates import coordinates_bp
    from .blueprints.formula_solver import bp as formula_solver_bp
    from .blueprints.alphabets import alphabets_bp
    from .blueprints.preferences import bp as preferences_bp
    from .blueprints.logs import bp as logs_bp
    from .blueprints.notes import bp as notes_bp
    from .blueprints.checkers import bp as checkers_bp
    from .blueprints.geocache_images import bp as geocache_images_bp

    app.register_blueprint(zones_bp)
    app.register_blueprint(geocaches_bp)
    app.register_blueprint(plugins_bp)
    app.register_blueprint(tasks_bp)
    app.register_blueprint(coordinates_bp)
    app.register_blueprint(formula_solver_bp)
    app.register_blueprint(alphabets_bp)
    app.register_blueprint(preferences_bp)
    app.register_blueprint(logs_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(checkers_bp)
    app.register_blueprint(geocache_images_bp)

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
    
    if auto_discover_plugins and not is_migration and not is_testing:
        with app.app_context():
            plugin_manager.discover_plugins()
    else:
        logging.info(
            "Découverte des plugins ignorée (auto_discover=%s, migration=%s, tests=%s)",
            auto_discover_plugins,
            is_migration,
            is_testing
        )
    
    # Initialiser le blueprint plugins avec le manager
    init_plugin_manager(plugin_manager)
    
    if not plugin_manager.lazy_mode:
        plugin_manager.preload_enabled_plugins()
    
    # Stocker le manager dans l'app pour accès global
    app.plugin_manager = plugin_manager
    
    # Initialiser le TaskManager
    from .services import TaskManager
    
    task_manager = TaskManager(max_workers=task_max_workers, auto_start=task_auto_start)
    
    # Initialiser le blueprint tasks avec les managers
    init_task_manager(task_manager, plugin_manager)
    
    # Stocker le task manager dans l'app
    app.task_manager = task_manager

    return app


