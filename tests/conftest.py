"""Test configuration for Smart Cooling tests."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

# Mock homeassistant imports BEFORE any test imports happen
# This must be done at module level, not in a fixture
_ha_modules = {
    "homeassistant": MagicMock(),
    "homeassistant.core": MagicMock(),
    "homeassistant.config_entries": MagicMock(),
    "homeassistant.helpers": MagicMock(),
    "homeassistant.helpers.update_coordinator": MagicMock(),
    "homeassistant.helpers.device_registry": MagicMock(),
    "homeassistant.helpers.entity_platform": MagicMock(),
    "homeassistant.helpers.event": MagicMock(),
    "homeassistant.components.sensor": MagicMock(),
    "homeassistant.const": MagicMock(),
    "homeassistant.data_entry_flow": MagicMock(),
    "homeassistant.helpers.selector": MagicMock(),
    "homeassistant.util": MagicMock(),
    "homeassistant.util.dt": MagicMock(),
}

# Add ConfigEntry mock
_ha_modules["homeassistant.config_entries"].ConfigEntry = MagicMock

for mod_name, mock_obj in _ha_modules.items():
    sys.modules[mod_name] = mock_obj

import pytest
