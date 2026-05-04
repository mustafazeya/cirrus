#!/usr/bin/env python3
"""
Start the FastAPI application with custom logging configuration
"""
import uvicorn
from logging_config import LOGGING_CONFIG
from config import settings

# ============================================================================
# Server Startup with Centralized Logging Configuration
# ============================================================================
# To adjust log verbosity (INFO/DEBUG/WARNING/ERROR), edit logging_config.py
# Change APP_LOG_LEVEL, UVICORN_LOG_LEVEL, or ACCESS_LOG_ENABLED constants

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.SERVER_PORT,
        reload=True,
        log_config=LOGGING_CONFIG,
        access_log=False
    )
