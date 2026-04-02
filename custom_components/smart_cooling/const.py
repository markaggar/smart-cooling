"""Constants for Smart Cooling integration."""
from typing import Final

DOMAIN: Final = "smart_cooling"
MANUFACTURER: Final = "markaggar"

# =============================================================================
# GLOBAL CONFIGURATION (shared across all room instances)
# These sensors provide environment data used by all rooms
# =============================================================================

# Weather entity - must support hourly forecast via weather.get_forecasts service
# The hourly forecast array includes these attributes per forecast item:
#   - datetime: forecast time
#   - temperature: predicted outdoor temp
#   - wind_speed: wind speed (used for fan/window cooling calculations)
#   - humidity: outdoor humidity
#   - condition: weather condition string
CONF_WEATHER_ENTITY: Final = "weather_entity"

# Outdoor temperature sensor (can be standalone or from weather entity)
CONF_OUTDOOR_TEMP_SENSOR: Final = "outdoor_temp_sensor"

# Air Quality Index sensor - used to determine if windows/fans are safe  
CONF_AQI_SENSOR: Final = "aqi_sensor"

# =============================================================================
# PER-ROOM CONFIGURATION (unique per instance)
# Each room has its own physics simulation and output sensors
# =============================================================================

# Room identification
CONF_ROOM_NAME: Final = "room_name"

# Indoor sensors for this room
CONF_INDOOR_TEMP_SENSOR: Final = "indoor_temp_sensor"
CONF_INDOOR_HUMIDITY_SENSOR: Final = "indoor_humidity_sensor"

# Device state sensors for this room
CONF_WINDOW_SENSOR: Final = "window_sensor"
CONF_FAN_SENSOR: Final = "fan_sensor"
CONF_AC_SENSOR: Final = "ac_sensor"

# Target helpers for this room
# Target temperature: input_number helper (e.g., input_number.bedroom_target_temp)
CONF_TARGET_TEMP_ENTITY: Final = "target_temp_entity"

# Target time: input_datetime helper - the time by which to reach target temp
# (e.g., input_datetime.bedroom_target_time for "cool to 72°F by 10:30 PM")
CONF_TARGET_TIME_ENTITY: Final = "target_time_entity"

# Legacy - kept for migration
CONF_BEDTIME_ENTITY: Final = "bedtime_entity"

# =============================================================================
# OPTIONAL ADVANCED CONFIGURATION
# =============================================================================
CONF_AC_SETPOINT_ENTITY: Final = "ac_setpoint_entity"
CONF_AQI_THRESHOLD_ENTITY: Final = "aqi_threshold_entity"
CONF_TEMP_ADVANTAGE_ENTITY: Final = "temp_advantage_entity"
CONF_MIN_WIND_ENTITY: Final = "min_wind_entity"
CONF_COMFORT_TOLERANCE_ENTITY: Final = "comfort_tolerance_entity"

# Deprecated - wind speed now comes from weather hourly forecast
CONF_WIND_SPEED_SENSOR: Final = "wind_speed_sensor"
CONF_CLOUD_COVERAGE_SENSOR: Final = "cloud_coverage_sensor"
CONF_UV_INDEX_SENSOR: Final = "uv_index_sensor"

# =============================================================================
# GLOBAL DATA STORAGE
# =============================================================================
# Key for storing shared global config in hass.data[DOMAIN]
GLOBAL_CONFIG_KEY: Final = "_global_config"

# =============================================================================
# PHYSICS PARAMETERS
# =============================================================================
# These will be learned over time, stored per-room
DEFAULT_PHYSICS_PARAMS: Final = {
    "base_heat_gain_rate": 2.2,  # °F/hr passive heat gain
    "solar_gain_factor": 0.6,  # multiplier for solar heat
    "ac_cooling_rate_mild": 4.5,  # °F/hr when outdoor < 82°F
    "ac_cooling_rate_hot": 2.5,  # °F/hr when outdoor >= 82°F
    "natural_cooling_effectiveness": 0.05,  # natural ventilation coefficient
    "fan_cooling_effectiveness": 0.15,  # fan ventilation coefficient
    "fan_equivalent_wind_speed": 8.0,  # mph equivalent wind from fan
    "fan_boost_factor": 1.4,  # multiplier when fan + wind
    "thermal_transfer_coefficient": 0.1,  # heat transfer through walls
}

# =============================================================================
# LEARNING SYSTEM
# =============================================================================
CONF_LEARNING_ENABLED: Final = "learning_enabled"
CONF_LEARNING_RATE: Final = "learning_rate"
DEFAULT_LEARNING_RATE: Final = 0.1

# Tolerance in minutes beyond target time that is still acceptable
# e.g. 30 means: if fan reaches target by target_time+30min, prefer fan over AC
CONF_TOLERANCE_MINUTES: Final = "tolerance_minutes"
DEFAULT_TOLERANCE_MINUTES: Final = 30

# =============================================================================
# UPDATE INTERVAL
# =============================================================================
UPDATE_INTERVAL_SECONDS: Final = 60

# =============================================================================
# OUTPUT SENSOR IDS (created by this integration, per-room)
# =============================================================================
SENSOR_RECOMMENDATION: Final = "recommendation"
SENSOR_PREDICTED_TARGET_TEMP: Final = "predicted_target_temp"
SENSOR_COOLING_DEFICIT: Final = "cooling_deficit"
SENSOR_CONFIDENCE: Final = "prediction_confidence"
SENSOR_CURRENT_STRATEGY: Final = "current_strategy"
SENSOR_TIME_TO_TARGET: Final = "time_to_target"
SENSOR_WILL_REACH_TARGET_AT: Final = "will_reach_target_at"
SENSOR_ACTION_NEEDED_BY: Final = "action_needed_by"
SENSOR_REASONING: Final = "reasoning"

# Legacy alias
SENSOR_PREDICTED_BEDTIME_TEMP: Final = SENSOR_PREDICTED_TARGET_TEMP
