"""
Centralized logging configuration for CIRRUS application
Controls all logging behavior in one place
"""
import logging
import os

# ============================================================================
# LOGGING LEVEL CONFIGURATION - Set via LOG_LEVEL environment variable
# ============================================================================
# Get log level from env (default: INFO), with fallback if invalid level provided
APP_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
UVICORN_LOG_LEVEL = logging.WARNING  # Uvicorn server logs
AZURE_SDK_LOG_LEVEL = logging.WARNING  # Azure SDK logs (very verbose if INFO)
ACCESS_LOG_ENABLED = False        # Set to True to see HTTP request logs

# ============================================================================
# Uvicorn Configuration (used by start_server.py)
# ============================================================================
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "detailed": {
            "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "detailed": {
            "formatter": "detailed",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.NullHandler" if not ACCESS_LOG_ENABLED else "logging.StreamHandler",
        },
    },
    "loggers": {
        "": {  # Root logger
            "handlers": ["detailed"],
            "level": APP_LOG_LEVEL,
        },
        "uvicorn": {
            "handlers": ["default"],
            "level": UVICORN_LOG_LEVEL,
            "propagate": False,
        },
        "uvicorn.error": {
            "level": UVICORN_LOG_LEVEL,
        },
        "uvicorn.access": {
            "handlers": ["access"],
            "level": "ERROR" if not ACCESS_LOG_ENABLED else "INFO",
            "propagate": False,
        },
        # Azure SDK loggers (can be very verbose)
        "azure": {
            "level": AZURE_SDK_LOG_LEVEL,
            "propagate": True,
        },
        "azure.core": {
            "level": AZURE_SDK_LOG_LEVEL,
            "propagate": True,
        },
        "azure.identity": {
            "level": AZURE_SDK_LOG_LEVEL,
            "propagate": True,
        },
        "azure.monitor.query": {
            "level": logging.ERROR,  # Suppress datetime tzinfo warnings
            "propagate": True,
        },
        "azure.monitor.query._generated._serialization": {
            "level": logging.ERROR,  # Suppress "Datetime with no tzinfo will be considered UTC" warnings
            "propagate": False,
        },
        "azure.mgmt.costmanagement._serialization": {
            "level": logging.ERROR,  # Suppress "Datetime with no tzinfo will be considered UTC" warnings
            "propagate": False,
        },
        # APScheduler loggers (suppress verbose job scheduling logs)
        "apscheduler": {
            "level": logging.WARNING,
            "propagate": True,
        },
        "apscheduler.scheduler": {
            "level": logging.WARNING,
            "propagate": True,
        },
        "apscheduler.executors": {
            "level": logging.WARNING,
            "propagate": True,
        },
    },
}

# ============================================================================
# Helper function to configure logging programmatically (used by main.py)
# ============================================================================
def configure_logging():
    """
    Configure logging for the application.
    Call this from main.py to ensure consistent logging across the app.
    """
    import logging.config
    logging.config.dictConfig(LOGGING_CONFIG)
