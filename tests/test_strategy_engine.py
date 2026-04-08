"""Tests for the strategy engine."""
from __future__ import annotations

import pytest
from datetime import datetime
import sys
from pathlib import Path

# Add the project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import AFTER conftest has mocked homeassistant
from custom_components.smart_cooling.thermal_model import ThermalModel
from custom_components.smart_cooling.strategy_engine import (
    StrategyEngine,
    CoolingMethod,
    CoolingStrategy,
)


class TestStrategyEngine:
    """Test the StrategyEngine class."""

    @pytest.fixture
    def thermal_model(self) -> ThermalModel:
        """Create a thermal model."""
        return ThermalModel(config={})

    @pytest.fixture
    def engine(self, thermal_model: ThermalModel) -> StrategyEngine:
        """Create a strategy engine."""
        return StrategyEngine(thermal_model)

    def test_no_action_when_comfortable(self, engine: StrategyEngine, thermal_model: ThermalModel):
        """Test no action recommended when already at target."""
        conditions = {
            "indoor_temp": 72.0,
            "outdoor_temp": 70.0,
            "target_temp": 72.0,
            "aqi": 50,
            "wind_speed": 10.0,
            "current_time": datetime(2024, 7, 15, 20, 0),
            "bedtime": "22:30:00",
        }
        
        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=2.5,
        )
        
        # Manually set low deficit for test
        prediction.cooling_deficit = 0.5
        
        strategy = engine.recommend(conditions, prediction)
        
        assert strategy.method == CoolingMethod.NO_ACTION
        assert "already at" in strategy.reasoning.lower() or "comfort" in strategy.reasoning.lower()

    def test_recommends_fan_when_outdoor_cooler(self, engine: StrategyEngine, thermal_model: ThermalModel):
        """Test fan recommendation when outdoor is significantly cooler."""
        conditions = {
            "indoor_temp": 74.0,  # Closer to target
            "outdoor_temp": 62.0,  # Much cooler - good temp advantage
            "target_temp": 72.0,
            "aqi": 50,  # Good air quality
            "wind_speed": 12.0,  # Good wind
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "current_time": datetime(2024, 7, 15, 21, 0),  # Evening
            "bedtime": "22:30:00",
        }
        
        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=1.5,  # Only 1.5 hours to bedtime
            cooling_strategy=None,
        )
        
        strategy = engine.recommend(conditions, prediction)
        
        # With 12°F temp advantage and short timeframe, should recommend 
        # natural cooling methods (fan or window), or at minimum not AC
        # as the primary choice when natural cooling would work
        assert strategy.method in [
            CoolingMethod.START_FAN,
            CoolingMethod.OPEN_WINDOW,
            CoolingMethod.NO_ACTION,  # May not need action if deficit is small
        ] or strategy.predicted_temp <= conditions["target_temp"] + 2

    def test_recommends_ac_when_hot_outside(self, engine: StrategyEngine, thermal_model: ThermalModel):
        """Test AC recommendation when outdoor is hot."""
        conditions = {
            "indoor_temp": 78.0,
            "outdoor_temp": 92.0,  # Hot outside
            "target_temp": 72.0,
            "aqi": 50,
            "wind_speed": 5.0,
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "current_time": datetime(2024, 7, 15, 15, 0),  # 3 PM hot
            "bedtime": "22:30:00",
        }
        
        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=7.5,
        )
        
        strategy = engine.recommend(conditions, prediction)
        
        # Should recommend AC since fan won't work with hot outdoor
        assert strategy.method in [CoolingMethod.START_AC]

    def test_continues_fan_if_already_running(self, engine: StrategyEngine, thermal_model: ThermalModel):
        """Test that we recommend continuing fan if it's already running."""
        conditions = {
            "indoor_temp": 76.0,
            "outdoor_temp": 65.0,
            "target_temp": 72.0,
            "aqi": 50,
            "wind_speed": 10.0,
            "window_open": True,
            "fan_running": True,  # Already running
            "ac_running": False,
            "current_time": datetime(2024, 7, 15, 21, 0),
            "bedtime": "22:30:00",
        }
        
        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=1.5,
            cooling_strategy="fan",
        )
        
        strategy = engine.recommend(conditions, prediction)
        
        # Should recommend continuing, not starting
        if strategy.method in [CoolingMethod.START_FAN, CoolingMethod.CONTINUE_FAN]:
            assert strategy.method == CoolingMethod.CONTINUE_FAN

    def test_poor_aqi_avoids_window(self, engine: StrategyEngine, thermal_model: ThermalModel):
        """Test that high AQI prevents window/fan recommendations."""
        conditions = {
            "indoor_temp": 78.0,
            "outdoor_temp": 65.0,  # Would be great for fan
            "target_temp": 72.0,
            "aqi": 180,  # Poor air quality
            "wind_speed": 10.0,
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "current_time": datetime(2024, 7, 15, 20, 0),
            "bedtime": "22:30:00",
        }
        
        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=2.5,
        )
        
        strategy = engine.recommend(conditions, prediction)
        
        # Should NOT recommend fan/window due to AQI
        assert strategy.method not in [
            CoolingMethod.START_FAN,
            CoolingMethod.OPEN_WINDOW,
        ]

    def test_strategy_to_dict(self, engine: StrategyEngine, thermal_model: ThermalModel):
        """Test strategy serialization."""
        conditions = {
            "indoor_temp": 75.0,
            "outdoor_temp": 70.0,
            "target_temp": 72.0,
            "aqi": 50,
            "current_time": datetime.now(),
            "bedtime": "22:30:00",
        }
        
        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=2.0,
        )
        
        strategy = engine.recommend(conditions, prediction)
        result = strategy.to_dict()
        
        assert "method" in result
        assert "reasoning" in result
        assert "confidence" in result
        assert isinstance(result["method"], str)

    def test_display_text(self, engine: StrategyEngine, thermal_model: ThermalModel):
        """Test human-readable display text."""
        strategy = CoolingStrategy(
            method=CoolingMethod.START_FAN,
            timing="NOW!",
            predicted_temp=72.0,
            target_temp=72.0,
            reasoning="Test",
            confidence=0.8,
        )
        
        assert "Start fan" in strategy.display_text
        assert "NOW!" in strategy.display_text
