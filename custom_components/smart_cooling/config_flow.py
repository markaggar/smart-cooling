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
    GLOBAL_CONFIG_KEY,
    # Global config keys
    CONF_WEATHER_ENTITY,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_AQI_SENSOR,
    # Room config keys
    CONF_ROOM_NAME,
    CONF_INDOOR_TEMP_SENSOR,
    CONF_INDOOR_HUMIDITY_SENSOR,
    CONF_WINDOW_SENSOR,
    CONF_FAN_SENSOR,
    CONF_AC_SENSOR,
    CONF_WINDOW_FACING,
    WINDOW_DIRECTION_OPTIONS,
    CONF_FAN_AVAILABLE,
    CONF_AC_AVAILABLE,
    CONF_AC_SETPOINT_ENTITY,
    CONF_TARGET_TEMP_ENTITY,
    CONF_TARGET_TIME_ENTITY,
    CONF_LEARNING_ENABLED,
    CONF_TOLERANCE_MINUTES,
    DEFAULT_TOLERANCE_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# GLOBAL CONFIGURATION SCHEMA
# Shared across all room instances - weather, outdoor temp, AQI
# =============================================================================
STEP_GLOBAL_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_WEATHER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather"),
        ),
        vol.Required(CONF_OUTDOOR_TEMP_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
        vol.Optional(CONF_AQI_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
    }
)


# =============================================================================
# ROOM CONFIGURATION SCHEMAS
# Unique per instance - each room has its own physics simulation
# =============================================================================
STEP_ROOM_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ROOM_NAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT),
        ),
        vol.Required(CONF_INDOOR_TEMP_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
        vol.Optional(CONF_INDOOR_HUMIDITY_SENSOR): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        ),
    }
)

STEP_ROOM_DEVICES_SCHEMA = vol.Schema(
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
        vol.Optional(CONF_WINDOW_FACING, default=[]): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=WINDOW_DIRECTION_OPTIONS,
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            ),
        ),
        vol.Optional(CONF_FAN_AVAILABLE, default=True): selector.BooleanSelector(),
        vol.Optional(CONF_AC_AVAILABLE, default=True): selector.BooleanSelector(),
        vol.Optional(CONF_AC_SETPOINT_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["climate", "input_number"]),
        ),
    }
)

STEP_ROOM_TARGETS_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_TARGET_TEMP_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="input_number"),
        ),
        vol.Optional(CONF_TARGET_TIME_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="input_datetime"),
        ),
        vol.Optional(
            CONF_TOLERANCE_MINUTES, default=DEFAULT_TOLERANCE_MINUTES
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="min"),
        ),
        vol.Optional(CONF_LEARNING_ENABLED, default=True): selector.BooleanSelector(),
    }
)


class SmartCoolingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Cooling.
    
    Architecture:
    - Global config (weather, outdoor temp, AQI) is shared across all rooms
    - Global config is stored in hass.data[DOMAIN][GLOBAL_CONFIG_KEY]
    - Each room is a separate config entry with its own sensors and physics
    - First setup collects global config, subsequent setups reuse it
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}
        self._global_data: dict[str, Any] = {}

    def _has_global_config(self) -> bool:
        """Check if global config already exists from another entry."""
        # Check hass.data for global config
        if DOMAIN in self.hass.data and GLOBAL_CONFIG_KEY in self.hass.data[DOMAIN]:
            return True
        # Check existing entries for any smart_cooling config  
        entries = self._async_current_entries()
        return len(entries) > 0

    def _get_global_config(self) -> dict[str, Any]:
        """Get existing global config from hass.data or first entry."""
        # Try hass.data first
        if DOMAIN in self.hass.data and GLOBAL_CONFIG_KEY in self.hass.data[DOMAIN]:
            return self.hass.data[DOMAIN][GLOBAL_CONFIG_KEY]
        # Fallback to first existing entry
        entries = self._async_current_entries()
        if entries:
            first_entry = entries[0]
            return {
                CONF_WEATHER_ENTITY: first_entry.data.get(CONF_WEATHER_ENTITY),
                CONF_OUTDOOR_TEMP_SENSOR: first_entry.data.get(CONF_OUTDOOR_TEMP_SENSOR),
                CONF_AQI_SENSOR: first_entry.data.get(CONF_AQI_SENSOR),
            }
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - route to global or room config."""
        if self._has_global_config():
            # Global config exists, skip to room setup
            self._global_data = self._get_global_config()
            return await self.async_step_room()
        # First setup - collect global config
        return await self.async_step_global()

    async def async_step_global(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle global configuration - weather, outdoor temp, AQI."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._global_data = {
                CONF_WEATHER_ENTITY: user_input.get(CONF_WEATHER_ENTITY),
                CONF_OUTDOOR_TEMP_SENSOR: user_input.get(CONF_OUTDOOR_TEMP_SENSOR),
                CONF_AQI_SENSOR: user_input.get(CONF_AQI_SENSOR),
            }
            return await self.async_step_room()

        return self.async_show_form(
            step_id="global",
            data_schema=STEP_GLOBAL_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "title": "Smart Cooling - Global Settings",
            },
        )

    async def async_step_room(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle room identification and indoor sensors."""
        errors: dict[str, str] = {}

        if user_input is not None:
            room_name = user_input.get(CONF_ROOM_NAME, "").strip()
            
            # Check for duplicate room names
            for entry in self._async_current_entries():
                if entry.data.get(CONF_ROOM_NAME, "").lower() == room_name.lower():
                    errors[CONF_ROOM_NAME] = "room_already_configured"
                    break
            
            if not errors:
                self._data.update(user_input)
                return await self.async_step_devices()

        return self.async_show_form(
            step_id="room",
            data_schema=STEP_ROOM_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle room device sensor selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_targets()

        return self.async_show_form(
            step_id="devices",
            data_schema=STEP_ROOM_DEVICES_SCHEMA,
            errors=errors,
        )

    async def async_step_targets(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle target temperature and time configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            
            # Merge global and room config
            final_data = {**self._global_data, **self._data}
            room_name = self._data.get(CONF_ROOM_NAME, "Room")
            
            # Store global config in hass.data for future instances
            if DOMAIN not in self.hass.data:
                self.hass.data[DOMAIN] = {}
            self.hass.data[DOMAIN][GLOBAL_CONFIG_KEY] = self._global_data
            
            return self.async_create_entry(
                title=f"Smart Cooling - {room_name}",
                data=final_data,
            )

        return self.async_show_form(
            step_id="targets",
            data_schema=STEP_ROOM_TARGETS_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return SmartCoolingOptionsFlow()


class SmartCoolingOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Smart Cooling."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options - choose global or room settings."""
        return self.async_show_menu(
            step_id="init",
            menu_options={
                "room_settings": "Room Settings",
                "global_settings": "Global Settings (affects all rooms)",
            },
        )

    async def async_step_room_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage room-specific options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}

        # Build schema with suggested values (not defaults) for optional entity fields
        schema_dict = {}
        
        # Required field with default
        schema_dict[vol.Optional(
            CONF_INDOOR_TEMP_SENSOR,
            description={"suggested_value": current.get(CONF_INDOOR_TEMP_SENSOR)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        )
        
        # Optional fields with suggested values
        schema_dict[vol.Optional(
            CONF_INDOOR_HUMIDITY_SENSOR,
            description={"suggested_value": current.get(CONF_INDOOR_HUMIDITY_SENSOR)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor"),
        )
        schema_dict[vol.Optional(
            CONF_WINDOW_SENSOR,
            description={"suggested_value": current.get(CONF_WINDOW_SENSOR)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor"),
        )
        schema_dict[vol.Optional(
            CONF_FAN_SENSOR,
            description={"suggested_value": current.get(CONF_FAN_SENSOR)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor"),
        )
        schema_dict[vol.Optional(
            CONF_AC_SENSOR,
            description={"suggested_value": current.get(CONF_AC_SENSOR)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="binary_sensor"),
        )
        # Window facing — multi-select checkboxes for compass directions
        schema_dict[vol.Optional(
            CONF_WINDOW_FACING,
            default=current.get(CONF_WINDOW_FACING, []),
        )] = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=WINDOW_DIRECTION_OPTIONS,
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            ),
        )
        schema_dict[vol.Optional(
            CONF_FAN_AVAILABLE,
            default=current.get(CONF_FAN_AVAILABLE, True),
        )] = selector.BooleanSelector()
        schema_dict[vol.Optional(
            CONF_AC_AVAILABLE,
            default=current.get(CONF_AC_AVAILABLE, True),
        )] = selector.BooleanSelector()
        schema_dict[vol.Optional(
            CONF_AC_SETPOINT_ENTITY,
            description={"suggested_value": current.get(CONF_AC_SETPOINT_ENTITY)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["climate", "input_number"]),
        )
        schema_dict[vol.Optional(
            CONF_TARGET_TEMP_ENTITY,
            description={"suggested_value": current.get(CONF_TARGET_TEMP_ENTITY)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="input_number"),
        )
        schema_dict[vol.Optional(
            CONF_TARGET_TIME_ENTITY,
            description={"suggested_value": current.get(CONF_TARGET_TIME_ENTITY)},
        )] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="input_datetime"),
        )
        schema_dict[vol.Optional(
            CONF_TOLERANCE_MINUTES,
            default=current.get(CONF_TOLERANCE_MINUTES, DEFAULT_TOLERANCE_MINUTES),
        )] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=120, step=5, unit_of_measurement="min"),
        )
        schema_dict[vol.Optional(
            CONF_LEARNING_ENABLED,
            default=current.get(CONF_LEARNING_ENABLED, True),
        )] = selector.BooleanSelector()

        return self.async_show_form(
            step_id="room_settings",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_global_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage global options - updates all instances."""
        if user_input is not None:
            # Update global config in hass.data
            if DOMAIN in self.hass.data:
                self.hass.data[DOMAIN][GLOBAL_CONFIG_KEY] = {
                    CONF_WEATHER_ENTITY: user_input.get(CONF_WEATHER_ENTITY),
                    CONF_OUTDOOR_TEMP_SENSOR: user_input.get(CONF_OUTDOOR_TEMP_SENSOR),
                    CONF_AQI_SENSOR: user_input.get(CONF_AQI_SENSOR),
                }
            return self.async_create_entry(title="", data=user_input)

        current = {**self.config_entry.data, **self.config_entry.options}
        
        # Also check hass.data for global config
        if DOMAIN in self.hass.data and GLOBAL_CONFIG_KEY in self.hass.data[DOMAIN]:
            global_config = self.hass.data[DOMAIN][GLOBAL_CONFIG_KEY]
            current.update(global_config)

        global_options_schema = vol.Schema(
            {
                vol.Required(
                    CONF_WEATHER_ENTITY,
                    description={"suggested_value": current.get(CONF_WEATHER_ENTITY)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="weather"),
                ),
                vol.Required(
                    CONF_OUTDOOR_TEMP_SENSOR,
                    description={"suggested_value": current.get(CONF_OUTDOOR_TEMP_SENSOR)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor"),
                ),
                vol.Optional(
                    CONF_AQI_SENSOR,
                    description={"suggested_value": current.get(CONF_AQI_SENSOR)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor"),
                ),
            }
        )

        return self.async_show_form(
            step_id="global_settings",
            data_schema=global_options_schema,
        )
