"""
Shared fixtures and config for the test suite.
"""
import sys
import os
import pytest

# Make backend the root so imports like `from services.x import Y` resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Prevent real .env loading from polluting tests
os.environ.setdefault("AZURE_TENANT_ID", "test-tenant")
os.environ.setdefault("OPENAI_DEPLOYMENT_NAME", "test-model")
os.environ.setdefault("OPENAI_API_BASE", "https://test.openai.azure.com")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
