"""Config flow for Smart Cooling integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_INDOOR_HUMIDITY_SENSOR,
    CONF_AQI_SENSOR,
    CONF_WIND_SPEED_SENSOR,
    CONF_WEATHER_ENTITY,
    CONF_WINDOW_SENSOR,
    CONF_FAN_SENSOR,
    CONF_AC_SENSOR,
    CONF_TARGET_TEMP_ENTITY,
    CONF_BEDTIME_ENTITY,
    CONF_LEARNING_ENABLED,
)

_LOGGER = logging.getLogger(__name__)


STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_INDOOR_TEMP_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
        vol.Required(CONF_OUTDOOR_TEMP_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
        vol.Optional(CONF_INDOOR_HUMIDITY_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
        vol.Optional(CONF_AQI_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
        vol.Optional(CONF_WIND_SPEED_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
        vol.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather"),
        ),
    }
)

STEP_DEVICES_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_WINDOW_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor"),
        ),
        vol.Optional(CONF_FAN_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor"),
        ),
        vol.Optional(CONF_AC_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor"),
        ),
    }
)

STEP_TARGETS_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TARGET_TEMP_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="input_number"),
        ),
        vol.Optional(CONF_BEDTIME_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="input_datetime"),
        ),
        vol.Optional(CONF_LEARNING_ENABLED, default=True): selector.BooleanSelector(),
    }
)


class SmartCoolingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Cooling."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - sensor selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "title": "Smart Cooling",
            },
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device sensor selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_targets()

        return self.async_show_form(
            step_id="devices",
            data_schema=STEP_DEVICES_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle target/setpoint configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            
            # Create the config entry
            return self.async_create_entry(
                title="Smart Cooling",
                data=self._data,
            )

        return self.async_show_form(
            step_id="targets",
            data_schema=STEP_TARGETS_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SmartCoolingOptionsFlow(config_entry)


class SmartCoolingOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart Cooling."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Merge current data and options
        current = {**self.config_entry.data, **self.config_entry.options}

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_INDOOR_TEMP_SENSOR,
                    default=current.get(CONF_INDOOR_TEMP_SENSOR, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor"),
                ),
                vol.Optional(
                    CONF_OUTDOOR_TEMP_SENSOR,
                    default=current.get(CONF_OUTDOOR_TEMP_SENSOR, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor"),
                ),
                vol.Optional(
                    CONF_WEATHER_ENTITY,
                    default=current.get(CONF_WEATHER_ENTITY, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather"),
                ),
                vol.Optional(
                    CONF_LEARNING_ENABLED,
                    default=current.get(CONF_LEARNING_ENABLED, True),
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
        )
