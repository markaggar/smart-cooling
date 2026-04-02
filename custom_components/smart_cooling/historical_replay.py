"""Historical data replay system for testing and learning.

This module loads historical sensor data (from HA recorder or CSV export) and
replays it through the thermal model to validate predictions and tune parameters.

CSV import uses the standard HA history export format:
    entity_id,state,last_changed
The caller provides entity_id-to-role mappings so no column names are hardcoded.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

_LOGGER = logging.getLogger(__name__)


@dataclass
class HistoricalDataPoint:
    """A single point in time with all sensor readings."""
    
    timestamp: datetime
    indoor_temp: float
    outdoor_temp: float
    target_temp: float = 72.0
    humidity: float = 50.0
    aqi: float = 50.0
    wind_speed: float = 5.0
    cloud_coverage: float = 50.0
    uv_index: float = 0.0
    window_open: bool = False
    fan_running: bool = False
    ac_running: bool = False
    
    def to_conditions_dict(self) -> dict[str, Any]:
        """Convert to format expected by thermal model."""
        return {
            "indoor_temp": self.indoor_temp,
            "outdoor_temp": self.outdoor_temp,
            "target_temp": self.target_temp,
            "humidity": self.humidity,
            "aqi": self.aqi,
            "wind_speed": self.wind_speed,
            "cloud_coverage": self.cloud_coverage,
            "uv_index": self.uv_index,
            "window_open": self.window_open,
            "fan_running": self.fan_running,
            "ac_running": self.ac_running,
            "current_time": self.timestamp,
            "forecast": [],  # Historical data doesn't have forecasts
        }


@dataclass
class ReplayResult:
    """Result of replaying historical data through the model."""
    
    timestamp: datetime
    actual_temp: float
    predicted_temp: float
    error: float  # actual - predicted
    conditions: dict[str, Any]
    strategy_recommended: str


class HistoricalDataLoader:
    """Load historical data from various sources."""

    def __init__(self) -> None:
        """Initialize loader."""
        if not HAS_PANDAS:
            raise ImportError("pandas is required for historical data loading")

    def load_from_excel(
        self,
        file_path: str | Path,
        sheet_name: str | int = 0,
        column_mapping: dict[str, str] | None = None,
    ) -> list[HistoricalDataPoint]:
        """Load historical data from Excel spreadsheet.
        
        Args:
            file_path: Path to Excel file
            sheet_name: Sheet to load (name or index)
            column_mapping: Optional mapping from Excel columns to data fields.
                           Keys are field names (indoor_temp, outdoor_temp, etc.)
                           Values are Excel column names.
        
        Returns:
            List of HistoricalDataPoint objects
        """
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        return self._dataframe_to_points(df, column_mapping)

    def load_from_csv(
        self,
        file_path: str | Path,
        column_mapping: dict[str, str] | None = None,
    ) -> list[HistoricalDataPoint]:
        """Load historical data from CSV file."""
        df = pd.read_csv(file_path)
        return self._dataframe_to_points(df, column_mapping)

    def load_from_ha_history(
        self,
        history_data: list[dict[str, Any]],
        indoor_entity: str,
        outdoor_entity: str,
    ) -> list[HistoricalDataPoint]:
        """Load from Home Assistant history export format.
        
        Expects format from HA's history API or recorder database.
        """
        # Group by timestamp
        points_by_time: dict[datetime, dict[str, Any]] = {}
        
        for record in history_data:
            entity_id = record.get("entity_id", "")
            state = record.get("state")
            timestamp_str = record.get("last_changed") or record.get("timestamp")
            
            if state in ("unknown", "unavailable", None):
                continue
            
            try:
                if isinstance(timestamp_str, str):
                    timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                else:
                    timestamp = timestamp_str
                
                # Round to nearest minute for grouping
                timestamp = timestamp.replace(second=0, microsecond=0)
                
                if timestamp not in points_by_time:
                    points_by_time[timestamp] = {}
                
                if entity_id == indoor_entity:
                    points_by_time[timestamp]["indoor_temp"] = float(state)
                elif entity_id == outdoor_entity:
                    points_by_time[timestamp]["outdoor_temp"] = float(state)
                    
            except (ValueError, TypeError) as err:
                _LOGGER.debug("Skipping record due to error: %s", err)
                continue
        
        # Convert to data points (only include timestamps with both values)
        points = []
        for timestamp, values in sorted(points_by_time.items()):
            if "indoor_temp" in values and "outdoor_temp" in values:
                points.append(HistoricalDataPoint(
                    timestamp=timestamp,
                    indoor_temp=values["indoor_temp"],
                    outdoor_temp=values["outdoor_temp"],
                ))
        
        return points

    def _dataframe_to_points(
        self,
        df: pd.DataFrame,
        column_mapping: dict[str, str] | None,
    ) -> list[HistoricalDataPoint]:
        """Convert pandas DataFrame to list of data points."""
        # Default column mapping
        default_mapping = {
            "timestamp": "timestamp",
            "indoor_temp": "indoor_temp",
            "outdoor_temp": "outdoor_temp",
            "target_temp": "target_temp",
            "humidity": "humidity",
            "aqi": "aqi",
            "wind_speed": "wind_speed",
            "cloud_coverage": "cloud_coverage",
            "uv_index": "uv_index",
            "window_open": "window_open",
            "fan_running": "fan_running",
            "ac_running": "ac_running",
        }
        
        if column_mapping:
            default_mapping.update(column_mapping)
        
        # Find available columns
        available_cols = {k: v for k, v in default_mapping.items() if v in df.columns}
        
        if "timestamp" not in available_cols:
            # Try to find a datetime-like column
            for col in df.columns:
                if "date" in col.lower() or "time" in col.lower():
                    available_cols["timestamp"] = col
                    break
        
        if "indoor_temp" not in available_cols or "outdoor_temp" not in available_cols:
            raise ValueError("CSV/Excel must have indoor_temp and outdoor_temp columns")
        
        points = []
        for _, row in df.iterrows():
            try:
                # Parse timestamp
                ts_val = row[available_cols["timestamp"]]
                if isinstance(ts_val, str):
                    timestamp = datetime.fromisoformat(ts_val)
                elif isinstance(ts_val, pd.Timestamp):
                    timestamp = ts_val.to_pydatetime()
                else:
                    timestamp = datetime.now()  # Fallback
                
                # Build data point
                point = HistoricalDataPoint(
                    timestamp=timestamp,
                    indoor_temp=float(row[available_cols["indoor_temp"]]),
                    outdoor_temp=float(row[available_cols["outdoor_temp"]]),
                    target_temp=float(row.get(available_cols.get("target_temp", ""), 72.0) or 72.0),
                    humidity=float(row.get(available_cols.get("humidity", ""), 50.0) or 50.0),
                    aqi=float(row.get(available_cols.get("aqi", ""), 50.0) or 50.0),
                    wind_speed=float(row.get(available_cols.get("wind_speed", ""), 5.0) or 5.0),
                )
                points.append(point)
                
            except (ValueError, TypeError, KeyError) as err:
                _LOGGER.debug("Skipping row due to error: %s", err)
                continue
        
        return points


class HistoricalReplayEngine:
    """Replay historical data through the model to validate and learn."""

    def __init__(self, thermal_model, strategy_engine) -> None:
        """Initialize with model and strategy engine."""
        self.thermal_model = thermal_model
        self.strategy_engine = strategy_engine

    def replay_data(
        self,
        data_points: list[HistoricalDataPoint],
        prediction_horizon_hours: float = 4.0,
    ) -> list[ReplayResult]:
        """Replay historical data and compare predictions with actuals.
        
        For each point, predict temperature `prediction_horizon_hours` ahead
        and compare with actual temperature at that time.
        
        Args:
            data_points: Sorted list of historical data points
            prediction_horizon_hours: How far ahead to predict
            
        Returns:
            List of replay results with errors
        """
        results = []
        
        # Index data points by timestamp for fast lookup
        points_by_time = {p.timestamp: p for p in data_points}
        
        for point in data_points:
            # Find the actual temperature at prediction_horizon_hours ahead
            target_time = point.timestamp + timedelta(hours=prediction_horizon_hours)
            
            # Find closest actual data point
            actual_point = self._find_closest_point(points_by_time, target_time, tolerance_minutes=30)
            
            if actual_point is None:
                # No actual data for comparison
                continue
            
            # Run prediction from this point
            conditions = point.to_conditions_dict()
            conditions["bedtime"] = "22:30:00"  # Default
            
            prediction = self.thermal_model.predict_temperature(
                current_conditions=conditions,
                hours_ahead=prediction_horizon_hours,
            )
            
            strategy = self.strategy_engine.recommend(
                current_conditions=conditions,
                prediction=prediction,
            )
            
            error = actual_point.indoor_temp - prediction.predicted_bedtime_temp
            
            results.append(ReplayResult(
                timestamp=point.timestamp,
                actual_temp=actual_point.indoor_temp,
                predicted_temp=prediction.predicted_bedtime_temp,
                error=error,
                conditions=conditions,
                strategy_recommended=strategy.method.value,
            ))
        
        return results

    def _find_closest_point(
        self,
        points_by_time: dict[datetime, HistoricalDataPoint],
        target_time: datetime,
        tolerance_minutes: int = 30,
    ) -> HistoricalDataPoint | None:
        """Find the closest data point to target time within tolerance."""
        tolerance = timedelta(minutes=tolerance_minutes)
        
        closest_point = None
        closest_diff = None
        
        for timestamp, point in points_by_time.items():
            diff = abs((timestamp - target_time).total_seconds())
            if diff <= tolerance.total_seconds():
                if closest_diff is None or diff < closest_diff:
                    closest_diff = diff
                    closest_point = point
        
        return closest_point

    def calculate_metrics(self, results: list[ReplayResult]) -> dict[str, float]:
        """Calculate accuracy metrics from replay results."""
        if not results:
            return {}
        
        errors = [r.error for r in results]
        abs_errors = [abs(e) for e in errors]
        
        return {
            "count": len(results),
            "mean_error": sum(errors) / len(errors),  # Bias
            "mean_absolute_error": sum(abs_errors) / len(abs_errors),
            "max_error": max(abs_errors),
            "min_error": min(abs_errors),
            "rmse": (sum(e**2 for e in errors) / len(errors)) ** 0.5,
        }

    def suggest_parameter_adjustments(
        self, results: list[ReplayResult]
    ) -> dict[str, float]:
        """Analyze errors and suggest parameter adjustments."""
        metrics = self.calculate_metrics(results)
        
        if not metrics:
            return {}
        
        suggestions = {}
        mean_error = metrics["mean_error"]
        
        # If we consistently predict too cold (mean_error > 0), increase heat gain
        # If we consistently predict too hot (mean_error < 0), decrease heat gain
        if abs(mean_error) > 0.5:
            current_heat_gain = self.thermal_model.params.get("base_heat_gain_rate", 2.2)
            # Adjust proportionally to error
            adjustment = mean_error * 0.1
            suggestions["base_heat_gain_rate"] = round(
                max(0.5, min(5.0, current_heat_gain + adjustment)), 2
            )
            _LOGGER.info(
                "Suggesting heat gain adjustment: %.2f -> %.2f (mean error: %.2f)",
                current_heat_gain,
                suggestions["base_heat_gain_rate"],
                mean_error,
            )
        
        return suggestions


def generate_synthetic_data(
    start_time: datetime,
    hours: int,
    scenario: str = "hot_day",
) -> list[HistoricalDataPoint]:
    """Generate synthetic historical data for testing.
    
    Args:
        start_time: When to start generating data
        hours: How many hours of data to generate
        scenario: One of "hot_day", "cool_day", "mild_day"
        
    Returns:
        List of synthetic data points (one per hour)
    """
    points = []
    
    # Scenario parameters
    scenarios = {
        "hot_day": {
            "outdoor_base": 85,
            "outdoor_amplitude": 10,  # Peak at 2pm
            "indoor_base": 75,
            "indoor_follow": 0.3,  # How much indoor follows outdoor
        },
        "cool_day": {
            "outdoor_base": 65,
            "outdoor_amplitude": 8,
            "indoor_base": 72,
            "indoor_follow": 0.2,
        },
        "mild_day": {
            "outdoor_base": 72,
            "outdoor_amplitude": 6,
            "indoor_base": 73,
            "indoor_follow": 0.25,
        },
    }
    
    params = scenarios.get(scenario, scenarios["mild_day"])
    
    import math
    
    for hour in range(hours):
        current_time = start_time + timedelta(hours=hour)
        hour_of_day = current_time.hour
        
        # Sinusoidal outdoor temperature (peak at 2pm = hour 14)
        outdoor_temp = params["outdoor_base"] + params["outdoor_amplitude"] * math.sin(
            math.pi * (hour_of_day - 8) / 12  # Peak at 14:00
        )
        
        # Indoor temperature follows outdoor with lag and damping
        indoor_temp = params["indoor_base"] + params["indoor_follow"] * (
            outdoor_temp - params["outdoor_base"]
        )
        
        # Add some noise
        import random
        outdoor_temp += random.gauss(0, 0.5)
        indoor_temp += random.gauss(0, 0.3)
        
        # UV index based on time of day
        if 6 <= hour_of_day <= 20:
            uv_index = max(0, 8 * math.sin(math.pi * (hour_of_day - 6) / 14))
        else:
            uv_index = 0
        
        points.append(HistoricalDataPoint(
            timestamp=current_time,
            indoor_temp=round(indoor_temp, 1),
            outdoor_temp=round(outdoor_temp, 1),
            target_temp=72.0,
            humidity=50.0 + random.gauss(0, 5),
            uv_index=round(uv_index, 1),
            cloud_coverage=30.0 + random.gauss(0, 10),
        ))
    
    return points


async def async_load_from_recorder(
    hass: Any,
    entity_roles: dict[str, str],
    days: int = 30,
) -> list[HistoricalDataPoint]:
    """Load historical data directly from the HA recorder database.

    No CSV export required — uses the recorder that HA already maintains.

    Args:
        hass: HomeAssistant instance
        entity_roles: Mapping of role → entity_id, e.g.:
            {
                "indoor_temp":  "sensor.master_bed_temperature",
                "outdoor_temp": "sensor.outside_temperature",
                "fan_running":  "binary_sensor.window_fan",
                "ac_running":   "binary_sensor.ac_on",
                "window_open":  "binary_sensor.bedroom_window",
            }
            Only "indoor_temp" and "outdoor_temp" are required.
        days: How many days of history to load (default 30).

    Returns:
        Sorted list of HistoricalDataPoint objects, one per minute bucket
        where at least indoor + outdoor readings are available.
    """
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.history import get_significant_states
    from homeassistant.util import dt as dt_util

    required = {"indoor_temp", "outdoor_temp"}
    missing = required - entity_roles.keys()
    if missing:
        raise ValueError(f"entity_roles must include: {missing}")

    entity_ids = list(entity_roles.values())
    end_time = dt_util.utcnow()
    start_time = end_time - timedelta(days=days)

    _LOGGER.info(
        "Loading %d days of recorder history for %s", days, entity_ids
    )

    # Recorder queries are blocking — run in the executor thread
    recorder = get_instance(hass)
    states_by_entity: dict[str, list] = await recorder.async_add_executor_job(
        get_significant_states,
        hass,
        start_time,
        end_time,
        entity_ids,
        None,   # filters
        True,   # include_start_time_state
        True,   # significant_changes_only
        False,  # minimal_response
        False,  # no_attributes
    )

    # Build a reverse map: entity_id → role
    entity_to_role = {v: k for k, v in entity_roles.items()}

    # Bucket readings by minute
    buckets: dict[datetime, dict[str, Any]] = {}
    for entity_id, states in states_by_entity.items():
        role = entity_to_role.get(entity_id)
        if not role:
            continue
        for state in states:
            if state.state in ("unknown", "unavailable", ""):
                continue
            try:
                value: Any
                if role in ("fan_running", "ac_running", "window_open"):
                    value = state.state == "on"
                else:
                    value = float(state.state)

                # Round to nearest minute for alignment
                ts = state.last_changed.replace(second=0, microsecond=0)
                if ts not in buckets:
                    buckets[ts] = {}
                # Keep the latest value in the minute bucket
                buckets[ts][role] = value
            except (ValueError, TypeError):
                continue

    # Build data points from buckets that have the required fields
    points: list[HistoricalDataPoint] = []
    for ts in sorted(buckets):
        b = buckets[ts]
        if "indoor_temp" not in b or "outdoor_temp" not in b:
            continue
        points.append(HistoricalDataPoint(
            timestamp=ts,
            indoor_temp=b["indoor_temp"],
            outdoor_temp=b["outdoor_temp"],
            fan_running=b.get("fan_running", False),
            ac_running=b.get("ac_running", False),
            window_open=b.get("window_open", False),
        ))

    _LOGGER.info("Loaded %d data points from recorder", len(points))
    return points

