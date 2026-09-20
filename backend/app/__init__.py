"""
MiroFish Backend - Flask Application Factory
"""

import os
import warnings

# Suppress multiprocessing resource_tracker warnings (from third-party libraries like transformers)
# Must be set before all other imports
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger
from .runtime import install_runtime_hardening
from .health import install_health_routes


def create_app(config_class=Config):
    """Flask application factory function"""
    app = Flask(__name__)
    app.config.from_object(config_class)
    app.config['STRICT_RESOURCE_ID_VALIDATION'] = True

    # Configure JSON encoding: ensure Chinese displays directly (not as \uXXXX)
    # Flask >= 2.3 uses app.json.ensure_ascii, older versions use JSON_AS_ASCII config
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False

    # Setup logging
    logger = setup_logger('mirofish')

    # Only print startup info in reloader subprocess (avoid printing twice in debug mode)
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process

    if should_log_startup:
        logger.info("=" * 50)
        logger.info("MiroFish-Offline Backend starting...")
        logger.info("=" * 50)

    # Enable browser access only from explicitly configured local frontends.
    CORS(
        app,
        resources={r"/api/*": {"origins": app.config['MIROFISH_ALLOWED_ORIGINS']}},
        allow_headers=['Content-Type', 'X-MiroFish-Control-Token', 'X-Request-ID'],
        expose_headers=['X-Request-ID'],
    )

    request_logger = get_logger('mirofish.request')
    app.extensions['request_logger'] = request_logger
    install_runtime_hardening(app, logger)

    from .models.task import TaskManager
    app.extensions['task_manager'] = TaskManager(
        upload_folder=app.config.get('UPLOAD_FOLDER', Config.UPLOAD_FOLDER),
        lease_seconds=app.config.get('TASK_WORKER_LEASE_SECONDS', 30),
    )

    # --- Initialize Neo4jStorage singleton (DI via app.extensions) ---
    from .storage import Neo4jStorage
    try:
        readiness_timeout = float(app.config.get('READINESS_TIMEOUT_SECONDS', 2.0))
        neo4j_storage = Neo4jStorage(
            connection_timeout=readiness_timeout,
            connection_acquisition_timeout=readiness_timeout,
        )
        app.extensions['neo4j_storage'] = neo4j_storage
        if should_log_startup:
            logger.info("Neo4jStorage initialized (connected to %s)", app.config['NEO4J_URI'])
    except Exception as e:
        logger.error("Neo4jStorage initialization failed: %s", e)
        # Store None so endpoints can return 503 gracefully
        app.extensions['neo4j_storage'] = None

    # Register simulation process cleanup function (ensure all simulation processes terminate on server shutdown)
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("Simulation process cleanup function registered")

    # Register blueprints
    from .api import graph_bp, simulation_bp, report_bp
    app.register_blueprint(graph_bp, url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp, url_prefix='/api/report')

    install_health_routes(app)

    if should_log_startup:
        logger.info("MiroFish-Offline Backend startup complete")

    return app
