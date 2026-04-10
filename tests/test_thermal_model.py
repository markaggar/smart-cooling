"""Tests for the thermal model."""
from __future__ import annotations

import pytest
from datetime import datetime
import sys
from pathlib import Path

# Add the project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import AFTER conftest has mocked homeassistant
from custom_components.smart_cooling.thermal_model import ThermalModel, TemperaturePrediction
from custom_components.smart_cooling.const import DEFAULT_PHYSICS_PARAMS


class TestThermalModel:
    """Test the ThermalModel class."""

    @pytest.fixture
    def model(self) -> ThermalModel:
        """Create a thermal model with default config."""
        return ThermalModel(config={})

    def test_initialization(self, model: ThermalModel):
        """Test model initializes with default parameters."""
        assert model.params == DEFAULT_PHYSICS_PARAMS
        assert model.params["base_heat_gain_rate"] == DEFAULT_PHYSICS_PARAMS["base_heat_gain_rate"]

    def test_update_params(self, model: ThermalModel):
        """Test updating physics parameters."""
        model.update_params({"base_heat_gain_rate": 3.0})
        assert model.params["base_heat_gain_rate"] == 3.0
        # Other params unchanged
        assert model.params["ac_cooling_rate_mild"] == 4.5

    def test_heat_gain_daytime(self, model: ThermalModel):
        """Test heat gain calculation during daytime with solar gain."""
        # Expect higher heat gain at noon with low clouds and high UV
        heat_gain = model.calculate_heat_gain(
            hour=14,  # 2 PM
            outdoor_temp=85.0,
            indoor_temp=75.0,
            cloud_coverage=20.0,
            uv_index=8.0,
        )
        # Should be positive (house is gaining heat)
        assert heat_gain > 0
        assert heat_gain > model.params["base_heat_gain_rate"]  # Solar adds to base

    def test_heat_gain_nighttime(self, model: ThermalModel):
        """Test heat gain at night (no solar component)."""
        heat_gain = model.calculate_heat_gain(
            hour=2,  # 2 AM
            outdoor_temp=65.0,
            indoor_temp=75.0,
            cloud_coverage=50.0,
            uv_index=0.0,
        )
        # With cooler outside, heat gain could be negative (house loses heat)
        assert heat_gain < model.calculate_heat_gain(
            hour=14, outdoor_temp=85.0, indoor_temp=75.0
        )

    def test_fan_cooling_rate_good_conditions(self, model: ThermalModel):
        """Test fan cooling when outdoor is significantly cooler."""
        cooling = model.calculate_fan_cooling_rate(
            outdoor_temp=65.0,
            indoor_temp=78.0,
            wind_speed=10.0,
        )
        # Should be positive cooling rate
        assert cooling > 0

    def test_fan_cooling_rate_no_advantage(self, model: ThermalModel):
        """Test fan cooling when outdoor is same or warmer."""
        cooling = model.calculate_fan_cooling_rate(
            outdoor_temp=80.0,
            indoor_temp=75.0,
            wind_speed=10.0,
        )
        # Can't cool if outside is warmer
        assert cooling == 0

    def test_ac_cooling_rate_mild(self, model: ThermalModel):
        """Test AC cooling rate in mild conditions."""
        rate = model.calculate_ac_cooling_rate(outdoor_temp=78.0)
        assert rate == model.params["ac_cooling_rate_mild"]

    def test_ac_cooling_rate_hot(self, model: ThermalModel):
        """Test AC cooling rate when very hot outside."""
        rate = model.calculate_ac_cooling_rate(outdoor_temp=95.0)
        assert rate == model.params["ac_cooling_rate_hot"]
        assert rate < model.params["ac_cooling_rate_mild"]  # Less effective when hot

    def test_predict_temperature_no_cooling(self, model: ThermalModel):
        """Test temperature prediction without any cooling strategy."""
        conditions = {
            "indoor_temp": 75.0,
            "outdoor_temp": 85.0,
            "target_temp": 72.0,
            "current_time": datetime(2024, 7, 15, 16, 0),  # 4 PM
            "forecast": [],
        }
        
        prediction = model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=4.0,
            cooling_strategy=None,
        )
        
        assert isinstance(prediction, TemperaturePrediction)
        # Temperature should rise with outdoor hotter than indoor
        assert prediction.predicted_target_temp >= 75.0
        assert prediction.cooling_deficit > 0  # Above target
        assert len(prediction.hourly_predictions) == 5  # 0-4 hours

    def test_predict_temperature_with_fan(self, model: ThermalModel):
        """Test temperature prediction with fan cooling."""
        conditions = {
            "indoor_temp": 78.0,
            "outdoor_temp": 65.0,  # Cooler outside
            "target_temp": 72.0,
            "current_time": datetime(2024, 7, 15, 20, 0),  # 8 PM
            "forecast": [],
        }
        
        # Without cooling
        no_cooling = model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=4.0,
            cooling_strategy=None,
        )
        
        # With fan
        with_fan = model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=4.0,
            cooling_strategy="fan",
        )
        
        # Fan should result in lower temperature
        assert with_fan.predicted_target_temp < no_cooling.predicted_target_temp

    def test_predict_temperature_with_ac(self, model: ThermalModel):
        """Test temperature prediction with AC cooling."""
        conditions = {
            "indoor_temp": 80.0,
            "outdoor_temp": 95.0,  # Hot outside, can't use fan
            "target_temp": 72.0,
            "current_time": datetime(2024, 7, 15, 17, 0),
            "forecast": [],
        }
        
        # Without cooling
        no_cooling = model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=4.0,
            cooling_strategy=None,
        )
        
        with_ac = model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=4.0,
            cooling_strategy="ac",
        )
        
        # AC should cool relative to no cooling, even if not below starting temp
        # In extreme heat, AC slows heat gain but may not fully cool
        assert with_ac.predicted_target_temp < no_cooling.predicted_target_temp

    def test_prediction_to_dict(self, model: ThermalModel):
        """Test prediction serialization."""
        conditions = {
            "indoor_temp": 75.0,
            "outdoor_temp": 70.0,
            "target_temp": 72.0,
            "current_time": datetime.now(),
            "forecast": [],
        }
        
        prediction = model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=2.0,
        )
        
        result = prediction.to_dict()
        assert "predicted_target_temp" in result
        assert "cooling_deficit" in result
        assert "hourly_predictions" in result
        assert isinstance(result["predicted_target_temp"], float)

    # ------------------------------------------------------------------
    # Solar model: extended window and thermal lag
    # ------------------------------------------------------------------

    def test_heat_gain_morning_solar(self, model: ThermalModel):
        """Solar gain should now contribute at 10 AM (was zero before noon)."""
        heat_gain_10am = model.calculate_heat_gain(
            hour=10,
            outdoor_temp=75.0,
            indoor_temp=70.0,
            cloud_coverage=0.0,   # clear sky
            uv_index=7.0,
        )
        heat_gain_midnight = model.calculate_heat_gain(
            hour=0,
            outdoor_temp=75.0,
            indoor_temp=70.0,
            cloud_coverage=0.0,
            uv_index=0.0,
        )
        # On a clear morning, solar contribution should raise heat gain above
        # the midnight baseline (same temp differential, no UV at night).
        assert heat_gain_10am > heat_gain_midnight

    def test_heat_gain_solar_peaks_early_afternoon(self, model: ThermalModel):
        """Solar gain should be higher at 1 PM than at 5 PM (same UV, same cloud)."""
        common = dict(outdoor_temp=80.0, indoor_temp=72.0, cloud_coverage=10.0, uv_index=9.0)
        heat_1pm = model.calculate_heat_gain(hour=13, **common)
        heat_5pm = model.calculate_heat_gain(hour=17, **common)
        assert heat_1pm > heat_5pm

    def test_heat_gain_cloudy_less_than_clear(self, model: ThermalModel):
        """Heavy cloud cover should reduce solar gain at the same hour and UV."""
        common = dict(hour=13, outdoor_temp=80.0, indoor_temp=72.0, uv_index=6.0)
        heat_clear = model.calculate_heat_gain(cloud_coverage=5.0, **common)
        heat_cloudy = model.calculate_heat_gain(cloud_coverage=90.0, **common)
        assert heat_clear > heat_cloudy

    def test_thermal_lag_increases_evening_heat_gain(self, model: ThermalModel):
        """Afternoon solar load should increase heat gain in the evening hours."""
        common = dict(hour=20, outdoor_temp=65.0, indoor_temp=70.0,
                      cloud_coverage=50.0, uv_index=0.0)
        no_lag = model.calculate_heat_gain(afternoon_solar_load=0.0, **common)
        with_lag = model.calculate_heat_gain(afternoon_solar_load=0.8, **common)
        assert with_lag > no_lag

    def test_thermal_lag_decays_by_midnight(self, model: ThermalModel):
        """Thermal lag should be smaller at midnight than at 8 PM (same load)."""
        common = dict(outdoor_temp=62.0, indoor_temp=68.0,
                      cloud_coverage=20.0, uv_index=0.0, afternoon_solar_load=1.0)
        heat_8pm = model.calculate_heat_gain(hour=20, **common)
        heat_midnight = model.calculate_heat_gain(hour=0, **common)
        # Midnight (lag_hours=10) should have smaller thermal lag contribution
        assert heat_midnight < heat_8pm

    def test_thermal_lag_zero_without_afternoon_solar(self, model: ThermalModel):
        """When afternoon_solar_load=0, evening heat gain should equal no-forecast baseline."""
        heat_gain = model.calculate_heat_gain(
            hour=21,
            outdoor_temp=60.0,
            indoor_temp=70.0,
            cloud_coverage=80.0,
            uv_index=0.0,
            afternoon_solar_load=0.0,
        )
        # Should be the same result as not passing afternoon_solar_load at all
        baseline = model.calculate_heat_gain(
            hour=21,
            outdoor_temp=60.0,
            indoor_temp=70.0,
            cloud_coverage=80.0,
            uv_index=0.0,
        )
        assert heat_gain == baseline

    # ------------------------------------------------------------------
    # Comfort window: overnight drift and pre-cool target
    # ------------------------------------------------------------------

    def test_simulate_comfort_window_passive_drift(self, model: ThermalModel):
        """Room should warm up overnight with no cooling in a hot room."""
        conditions = {
            "indoor_temp": 68.0,
            "outdoor_temp": 72.0,
            "target_temp": 68.0,
            "outdoor_humidity": 55.0,
            "current_time": datetime(2024, 7, 15, 22, 0),
            "forecast": [],
        }
        result = model.simulate_comfort_window(
            current_conditions=conditions,
            start_temp=68.0,
            start_time=datetime(2024, 7, 15, 22, 0),
            window_hours=8.0,
            cooling_strategy=None,
        )
        # With outdoor 4°F warmer, room should end warmer than it started
        assert result["end_temp"] > 68.0
        assert result["peak_temp"] >= result["end_temp"]

    def test_simulate_comfort_window_ac_maintains_setpoint(self, model: ThermalModel):
        """Room with AC active should stay near target throughout the window."""
        conditions = {
            "indoor_temp": 68.0,
            "outdoor_temp": 75.0,
            "target_temp": 68.0,
            "outdoor_humidity": 50.0,
            "current_time": datetime(2024, 7, 15, 22, 0),
            "forecast": [],
        }
        result_passive = model.simulate_comfort_window(
            current_conditions=conditions,
            start_temp=68.0,
            start_time=datetime(2024, 7, 15, 22, 0),
            window_hours=8.0,
            cooling_strategy=None,
        )
        result_ac = model.simulate_comfort_window(
            current_conditions=conditions,
            start_temp=68.0,
            start_time=datetime(2024, 7, 15, 22, 0),
            window_hours=8.0,
            cooling_strategy="ac",
        )
        # AC should keep the room cooler than passive drift
        assert result_ac["peak_temp"] < result_passive["peak_temp"]

    def test_get_peak_afternoon_solar_from_forecast(self, model: ThermalModel):
        """Helper should find the highest UV/cloud-adjusted solar entry at 9-17h."""
        forecast = [
            {"datetime": datetime(2024, 7, 15, 10, 0), "uv_index": 5.0,
             "cloud_coverage": 0.0, "temperature": 78.0},
            {"datetime": datetime(2024, 7, 15, 13, 0), "uv_index": 9.0,
             "cloud_coverage": 10.0, "temperature": 85.0},
            {"datetime": datetime(2024, 7, 15, 20, 0), "uv_index": 0.0,
             "cloud_coverage": 20.0, "temperature": 70.0},
        ]
        load = model._get_peak_afternoon_solar(forecast)
        # 1 PM entry: 0.9 × 0.90 = 0.81; should be returned
        assert 0.8 < load <= 1.0

    def test_get_peak_afternoon_solar_empty_forecast(self, model: ThermalModel):
        """Empty forecast should safely return 0 (no thermal lag)."""
        assert model._get_peak_afternoon_solar([]) == 0.0

    def test_get_peak_afternoon_solar_night_only_forecast(self, model: ThermalModel):
        """Night-only forecast entries should return 0 (no afternoon data)."""
        forecast = [
            {"datetime": datetime(2024, 7, 15, 21, 0), "uv_index": 0.0,
             "cloud_coverage": 40.0, "temperature": 62.0},
            {"datetime": datetime(2024, 7, 15, 22, 0), "uv_index": 0.0,
             "cloud_coverage": 30.0, "temperature": 60.0},
        ]
        assert model._get_peak_afternoon_solar(forecast) == 0.0

    def test_tracked_solar_overrides_empty_evening_forecast(self, model: ThermalModel):
        """When forecast has no afternoon entries (evening run), the coordinator-
        tracked peak_afternoon_solar in current_conditions must still drive the
        thermal-lag term.  This is the correctness scenario: without the tracked
        value the model forgets stored wall-heat after ~6 PM."""
        night_forecast = [
            {"datetime": datetime(2024, 7, 15, 21, 0), "uv_index": 0.0,
             "cloud_coverage": 30.0, "temperature": 68.0},
            {"datetime": datetime(2024, 7, 15, 22, 0), "uv_index": 0.0,
             "cloud_coverage": 30.0, "temperature": 66.0},
        ]
        base_conditions = {
            "indoor_temp": 78.0,
            "outdoor_temp": 65.0,
            "target_temp": 70.0,
            "outdoor_humidity": 50.0,
            "current_time": datetime(2024, 7, 15, 21, 0),
            "forecast": night_forecast,
        }
        # Without tracked value: thermal lag is zero (no afternoon data in forecast)
        result_no_lag = model.predict_temperature(
            dict(base_conditions), hours_ahead=4.0, cooling_strategy=None
        )
        # With tracked value set by coordinator (0.8 = sunny afternoon)
        with_tracked = {**base_conditions, "peak_afternoon_solar": 0.8}
        result_with_lag = model.predict_temperature(
            with_tracked, hours_ahead=4.0, cooling_strategy=None
        )
        # Thermal lag adds stored heat → predicted temp with lag must be warmer
        assert result_with_lag.predicted_target_temp > result_no_lag.predicted_target_temp

    def test_forecast_solar_wins_when_higher_than_tracked(self, model: ThermalModel):
        """During the daytime the forecast may predict a higher afternoon peak than
        what has been observed so far.  The model should use the forecast value
        (i.e. take the max, not just the tracked value)."""
        daytime_forecast = [
            {"datetime": datetime(2024, 7, 15, 13, 0), "uv_index": 9.0,
             "cloud_coverage": 5.0, "temperature": 88.0},
        ]
        base_conditions = {
            "indoor_temp": 78.0,
            "outdoor_temp": 82.0,
            "target_temp": 70.0,
            "outdoor_humidity": 40.0,
            "current_time": datetime(2024, 7, 15, 10, 0),
            "forecast": daytime_forecast,
        }
        # Tracked value is low (early morning — little observed yet); forecast is high
        result_low_tracked = model.predict_temperature(
            {**base_conditions, "peak_afternoon_solar": 0.1},
            hours_ahead=6.0, cooling_strategy=None,
        )
        # No tracked at all — falls back to forecast scan only (~0.81)
        result_forecast_only = model.predict_temperature(
            dict(base_conditions), hours_ahead=6.0, cooling_strategy=None,
        )
        # Both must produce nearly the same result: max(0.81, 0.1)==0.81 ≈ max(0.81, 0)==0.81
        assert abs(
            result_low_tracked.predicted_target_temp
            - result_forecast_only.predicted_target_temp
        ) < 0.5
