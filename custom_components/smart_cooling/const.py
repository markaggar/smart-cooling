"""Constants for Smart Cooling integration."""
from typing import Final

DOMAIN: Final = "smart_cooling"
MANUFACTURER: Final = "markaggar"

# Configuration keys
CONF_INDOOR_TEMP_SENSOR: Final = "indoor_temp_sensor"
CONF_OUTDOOR_TEMP_SENSOR: Final = "outdoor_temp_sensor"
CONF_INDOOR_HUMIDITY_SENSOR: Final = "indoor_humidity_sensor"
CONF_AQI_SENSOR: Final = "aqi_sensor"
CONF_WIND_SPEED_SENSOR: Final = "wind_speed_sensor"
CONF_CLOUD_COVERAGE_SENSOR: Final = "cloud_coverage_sensor"
CONF_UV_INDEX_SENSOR: Final = "uv_index_sensor"
CONF_WEATHER_ENTITY: Final = "weather_entity"

# Device state sensors
CONF_WINDOW_SENSOR: Final = "window_sensor"
CONF_FAN_SENSOR: Final = "fan_sensor"
CONF_AC_SENSOR: Final = "ac_sensor"

# Target/setpoint entities
CONF_TARGET_TEMP_ENTITY: Final = "target_temp_entity"
CONF_BEDTIME_ENTITY: Final = "bedtime_entity"
CONF_AC_SETPOINT_ENTITY: Final = "ac_setpoint_entity"
CONF_AQI_THRESHOLD_ENTITY: Final = "aqi_threshold_entity"
CONF_TEMP_ADVANTAGE_ENTITY: Final = "temp_advantage_entity"
CONF_MIN_WIND_ENTITY: Final = "min_wind_entity"
CONF_COMFORT_TOLERANCE_ENTITY: Final = "comfort_tolerance_entity"

# Physics parameters - these will be learned over time
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

# Learning system
CONF_LEARNING_ENABLED: Final = "learning_enabled"
CONF_LEARNING_RATE: Final = "learning_rate"
DEFAULT_LEARNING_RATE: Final = 0.1

# Update interval
UPDATE_INTERVAL_SECONDS: Final = 60

# Sensor entity IDs (created by this integration)
SENSOR_RECOMMENDATION: Final = "recommendation"
SENSOR_PREDICTED_BEDTIME_TEMP: Final = "predicted_bedtime_temp"
SENSOR_COOLING_DEFICIT: Final = "cooling_deficit"
SENSOR_CONFIDENCE: Final = "prediction_confidence"
SENSOR_CURRENT_STRATEGY: Final = "current_strategy"
