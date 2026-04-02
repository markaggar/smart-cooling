"""Smart Cooling Integration for Home Assistant."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import SmartCoolingCoordinator

if TYPE_CHECKING:
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_CALIBRATE = "calibrate"
SERVICE_CALIBRATE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Optional("days", default=30): vol.All(int, vol.Range(min=1, max=365)),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Smart Cooling integration."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_calibrate(call: ServiceCall) -> None:
        """Run calibration from recorder history for a room instance."""
        entry_id = call.data["entry_id"]
        days = call.data.get("days", 30)
        coordinator: SmartCoolingCoordinator | None = hass.data[DOMAIN].get(entry_id)
        if coordinator is None:
            _LOGGER.error("calibrate: no coordinator found for entry_id=%s", entry_id)
            return
        result = await coordinator.async_calibrate_from_history(days=days)
        _LOGGER.info("Calibration result for %s: %s", coordinator.room_name, result)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CALIBRATE,
        handle_calibrate,
        schema=SERVICE_CALIBRATE_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Cooling from a config entry."""
    coordinator = SmartCoolingCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register listener for options updates - triggers reload
    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    return True


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
