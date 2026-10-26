"""Fixtures for testing the Marstek integration."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the project root is importable so ``custom_components.marstek``
# resolves when the Home Assistant loader scans sys.path for it.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations defined in the test directory."""
    yield
