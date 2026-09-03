"""pytest configuration for the Real-Time Voice Assistant test suite.

Defines custom marks and shared baseline fixtures used across all test modules.

Markers
-------
integration
    Tests that call real external APIs (weather, Gemini) or use the
    production assistant.db database.  These tests require:
      - A valid WEATHER_API_KEY environment variable
      - A valid GEMINI_API_KEY environment variable
      - Network access

    Run integration tests explicitly with:
        pytest -m integration

    Exclude integration tests (default CI behaviour):
        pytest -m "not integration"
"""

import pytest


def pytest_configure(config):
    """Register custom markers so pytest --strict-markers does not warn."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require real external APIs or the "
        "production database (deselect with '-m \"not integration\"').",
    )
