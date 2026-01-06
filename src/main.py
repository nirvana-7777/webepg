"""
Main entry point for EPG service.
"""

import logging
import logging.config
import signal
import sys

from .api.server import run_server
from .config import load_config
from .database.connection import close_db, initialize_db
from .database.schema import SchemaManager
from .scheduler.jobs import JobScheduler


def setup_logging(config: dict):
    """
    Configure logging based on configuration.

    Args:
        config: Logging configuration dictionary
    """
    level = config.get("level", "INFO")
    format_type = config.get("format", "text")

    if format_type == "json":
        # JSON structured logging
        import json

        class JsonFormatter(logging.Formatter):
            def format(self, record):
                log_obj = {
                    "timestamp": self.formatTime(record, self.datefmt),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                }

                if record.exc_info:
                    log_obj["exception"] = self.formatException(record.exc_info)

                return json.dumps(log_obj)

        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())

        logging.root.handlers = [handler]
        logging.root.setLevel(level)
    else:
        # Text logging
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def main():
    """Main application entry point."""
    # Load configuration
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    config = load_config(config_path)

    # Setup logging
    setup_logging(config.get_section("logging"))
    logger = logging.getLogger(__name__)

    logger.info("Starting EPG service")

    try:
        # Initialize database
        db_path = config.get("database.path")
        logger.info(f"Initializing database: {db_path}")
        SchemaManager.initialize_database(db_path)
        initialize_db(db_path)

        # Verify schema
        if not SchemaManager.verify_schema(db_path):
            logger.error("Database schema verification failed")
            sys.exit(1)

        # Initialize scheduler
        scheduler_config = config.get_section("scheduler")
        scheduler_config["retention_days"] = config.get("retention.days", 7)
        scheduler = JobScheduler(scheduler_config)

        # Setup graceful shutdown
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down gracefully...")
            scheduler.stop()
            close_db()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start scheduler
        logger.info("Starting job scheduler")
        scheduler.start()

        # Start HTTP server
        server_config = config.get_section("server")
        logger.info(
            f"Starting HTTP server on {server_config['host']}:{server_config['port']}"
        )
        run_server(server_config, scheduler)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # Cleanup
        try:
            scheduler.stop()
        except:
            pass

        try:
            close_db()
        except:
            pass

        logger.info("EPG service stopped")


if __name__ == "__main__":
    main()
