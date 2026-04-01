"""Tests for the historical data replay system."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add the project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import AFTER conftest has mocked homeassistant
from custom_components.smart_cooling.thermal_model import ThermalModel
from custom_components.smart_cooling.strategy_engine import StrategyEngine
from custom_components.smart_cooling.historical_replay import (
    HistoricalDataPoint,
    HistoricalDataLoader,
    HistoricalReplayEngine,
    ReplayResult,
    generate_synthetic_data,
)


class TestHistoricalDataPoint:
    """Test HistoricalDataPoint data class."""

    def test_to_conditions_dict(self):
        """Test conversion to conditions dictionary."""
        timestamp = datetime(2024, 7, 15, 14, 0)
        point = HistoricalDataPoint(
            timestamp=timestamp,
            indoor_temp=76.0,
            outdoor_temp=88.0,
            target_temp=72.0,
            humidity=55.0,
        )
        
        conditions = point.to_conditions_dict()
        
        assert conditions["indoor_temp"] == 76.0
        assert conditions["outdoor_temp"] == 88.0
        assert conditions["target_temp"] == 72.0
        assert conditions["current_time"] == timestamp
        assert conditions["forecast"] == []


class TestGenerateSyntheticData:
    """Test synthetic data generation."""

    def test_hot_day_scenario(self):
        """Test generating hot day data."""
        start = datetime(2024, 7, 15, 6, 0)
        points = generate_synthetic_data(start, hours=24, scenario="hot_day")
        
        assert len(points) == 24
        assert all(isinstance(p, HistoricalDataPoint) for p in points)
        
        # Hot day should have high outdoor temps
        max_outdoor = max(p.outdoor_temp for p in points)
        assert max_outdoor > 85

    def test_cool_day_scenario(self):
        """Test generating cool day data."""
        start = datetime(2024, 7, 15, 6, 0)
        points = generate_synthetic_data(start, hours=24, scenario="cool_day")
        
        assert len(points) == 24
        
        # Cool day should have lower outdoor temps
        max_outdoor = max(p.outdoor_temp for p in points)
        assert max_outdoor < 80

    def test_timestamps_are_sequential(self):
        """Test that timestamps are sequential hourly."""
        start = datetime(2024, 7, 15, 6, 0)
        points = generate_synthetic_data(start, hours=12)
        
        for i in range(1, len(points)):
            expected_time = points[0].timestamp + timedelta(hours=i)
            assert points[i].timestamp == expected_time


class TestHistoricalReplayEngine:
    """Test the historical replay engine."""

    @pytest.fixture
    def thermal_model(self) -> ThermalModel:
        """Create a thermal model."""
        return ThermalModel(config={})

    @pytest.fixture
    def strategy_engine(self, thermal_model: ThermalModel) -> StrategyEngine:
        """Create a strategy engine."""
        return StrategyEngine(thermal_model)

    @pytest.fixture
    def replay_engine(
        self, thermal_model: ThermalModel, strategy_engine: StrategyEngine
    ) -> HistoricalReplayEngine:
        """Create a replay engine."""
        return HistoricalReplayEngine(thermal_model, strategy_engine)

    def test_replay_data(self, replay_engine: HistoricalReplayEngine):
        """Test replaying synthetic data."""
        start = datetime(2024, 7, 15, 10, 0)
        points = generate_synthetic_data(start, hours=12, scenario="hot_day")
        
        # Use 4-hour prediction horizon
        results = replay_engine.replay_data(points, prediction_horizon_hours=4.0)
        
        # Should have results (some points may not have matching actuals)
        assert len(results) > 0
        assert all(isinstance(r, ReplayResult) for r in results)
        
        # Check result structure
        result = results[0]
        assert hasattr(result, "actual_temp")
        assert hasattr(result, "predicted_temp")
        assert hasattr(result, "error")

    def test_calculate_metrics(self, replay_engine: HistoricalReplayEngine):
        """Test metric calculation from results."""
        # Create mock results
        results = [
            ReplayResult(
                timestamp=datetime.now(),
                actual_temp=75.0,
                predicted_temp=76.0,
                error=-1.0,
                conditions={},
                strategy_recommended="fan",
            ),
            ReplayResult(
                timestamp=datetime.now(),
                actual_temp=74.0,
                predicted_temp=73.5,
                error=0.5,
                conditions={},
                strategy_recommended="fan",
            ),
            ReplayResult(
                timestamp=datetime.now(),
                actual_temp=76.0,
                predicted_temp=76.0,
                error=0.0,
                conditions={},
                strategy_recommended="ac",
            ),
        ]
        
        metrics = replay_engine.calculate_metrics(results)
        
        assert "count" in metrics
        assert metrics["count"] == 3
        assert "mean_error" in metrics
        assert "mean_absolute_error" in metrics
        assert "rmse" in metrics
        assert metrics["mean_absolute_error"] > 0

    def test_suggest_parameter_adjustments(self, replay_engine: HistoricalReplayEngine):
        """Test parameter adjustment suggestions."""
        # Create results with consistent bias (predictions too cold)
        results = [
            ReplayResult(
                timestamp=datetime.now() + timedelta(hours=i),
                actual_temp=78.0 + i * 0.1,
                predicted_temp=75.0 + i * 0.1,  # Always 3° too cold
                error=3.0,
                conditions={},
                strategy_recommended="fan",
            )
            for i in range(25)
        ]
        
        suggestions = replay_engine.suggest_parameter_adjustments(results)
        
        # Should suggest increasing heat gain (we're predicting too cold)
        if "base_heat_gain_rate" in suggestions:
            original = replay_engine.thermal_model.params["base_heat_gain_rate"]
            assert suggestions["base_heat_gain_rate"] > original

    def test_empty_results_metrics(self, replay_engine: HistoricalReplayEngine):
        """Test metric calculation with empty results."""
        metrics = replay_engine.calculate_metrics([])
        assert metrics == {}


class TestLearningIntegration:
    """Test the full learning loop with historical data."""

    def test_full_learning_cycle(self):
        """Test a complete cycle: generate data, replay, learn."""
        # Create model and engines
        model = ThermalModel(config={})
        strategy = StrategyEngine(model)
        replay = HistoricalReplayEngine(model, strategy)
        
        # Record original parameters
        original_heat_gain = model.params["base_heat_gain_rate"]
        
        # Generate 48 hours of data
        start = datetime(2024, 7, 15, 0, 0)
        points = generate_synthetic_data(start, hours=48, scenario="hot_day")
        
        # Replay and collect results
        results = replay.replay_data(points, prediction_horizon_hours=4.0)
        
        # Calculate metrics
        metrics = replay.calculate_metrics(results)
        
        # We should have meaningful results
        assert metrics["count"] > 10
        
        # Get suggestions
        suggestions = replay.suggest_parameter_adjustments(results)
        
        # If there were suggestions, apply them
        if suggestions:
            model.update_params(suggestions)
            # Verify params changed
            assert (
                model.params["base_heat_gain_rate"] != original_heat_gain
                or len(suggestions) == 0
            )
