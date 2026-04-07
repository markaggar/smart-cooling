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
        assert prediction.predicted_bedtime_temp >= 75.0
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
        assert with_fan.predicted_bedtime_temp < no_cooling.predicted_bedtime_temp

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
        assert with_ac.predicted_bedtime_temp < no_cooling.predicted_bedtime_temp

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
        assert "predicted_bedtime_temp" in result
        assert "cooling_deficit" in result
        assert "hourly_predictions" in result
        assert isinstance(result["predicted_bedtime_temp"], float)
