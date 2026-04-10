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

SERVICE_SET_PARAMS = "set_params"
_POSITIVE_FLOAT = vol.All(vol.Coerce(float), vol.Range(min=0.0))
SERVICE_SET_PARAMS_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        # Accept any subset of the physics params; unknown keys are rejected
        vol.Optional("base_heat_gain_rate"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=10.0)),
        vol.Optional("thermal_transfer_coefficient"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional("solar_gain_factor"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
        vol.Optional("ac_cooling_rate_mild"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=20.0)),
        vol.Optional("ac_cooling_rate_hot"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=20.0)),
        vol.Optional("fan_cooling_effectiveness"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional("natural_cooling_effectiveness"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Optional("fan_equivalent_wind_speed"): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=30.0)),
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

    async def handle_set_params(call: ServiceCall) -> None:
        """Manually override one or more physics parameters for a room."""
        entry_id = call.data["entry_id"]
        coordinator: SmartCoolingCoordinator | None = hass.data[DOMAIN].get(entry_id)
        if coordinator is None:
            _LOGGER.error("set_params: no coordinator found for entry_id=%s", entry_id)
            return
        params = {k: v for k, v in call.data.items() if k != "entry_id"}
        if not params:
            _LOGGER.warning("set_params: no parameters provided")
            return
        coordinator.thermal_model.update_params(params)
        await coordinator.learning_module.save_params(params)
        _LOGGER.info(
            "set_params: applied manual overrides for %s: %s",
            coordinator.room_name,
            params,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_PARAMS,
        handle_set_params,
        schema=SERVICE_SET_PARAMS_SCHEMA,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Smart Cooling from a config entry."""
    coordinator = SmartCoolingCoordinator(hass, entry)
    await coordinator.async_initialize()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register listener for options updates - triggers reload
    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    return True


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to a newer schema version.

    Currently at VERSION = 1 — no migration needed yet.
    Extend this function when bumping SmartCoolingConfigFlow.VERSION.
    """
    _LOGGER.debug(
        "Migrating smart_cooling entry %s from version %s",
        entry.entry_id,
        entry.version,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
