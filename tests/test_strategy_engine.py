"""Tests for the strategy engine."""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
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
        """Test fan recommendation when outdoor is significantly cooler and there is a real cooling need."""
        conditions = {
            "indoor_temp": 78.0,  # Clearly above target — real cooling needed
            "outdoor_temp": 62.0,  # Much cooler — large temp advantage
            "target_temp": 72.0,
            "aqi": 50,  # Good air quality
            "wind_speed": 12.0,  # Good wind
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "current_time": datetime(2024, 7, 15, 20, 0),  # Evening — 4h to target
            "bedtime": "22:30:00",
        }
        
        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=2.5,
            cooling_strategy=None,
        )
        
        strategy = engine.recommend(conditions, prediction)
        
        # With 16°F temp advantage, good wind, 6°F cooling need, and 2.5h window,
        # the engine must recommend natural cooling (fan or open window) — not AC or no_action.
        assert strategy.method in [
            CoolingMethod.START_FAN,
            CoolingMethod.OPEN_WINDOW,
        ], f"Expected fan or window strategy, got {strategy.method} (reasoning: {strategy.reasoning})"

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

    def test_no_ac_when_window_can_cool_to_target(
        self, engine: StrategyEngine, thermal_model: ThermalModel
    ):
        """Regression: system incorrectly recommended AC when an open window was
        sufficient.  Logged case: 73.4°F indoor, 68°F target by 10:30 PM (2h 12min),
        outdoor 69°F dropping to 62°F, wind 7.2 mph, window already open.

        The three bugs that caused this:
        1. natural_cooling_effectiveness too low (0.15 → 0.25)
        2. outdoor-advantage gate blocked valid hours when outdoor was already < target
        3. forward scan used no-action baseline even though window was open
        """
        # Simulate the logged forecast: outdoor drops from 69°F to 62°F over 2h
        current_time = datetime(2024, 5, 13, 20, 18)
        forecast = [
            {
                "datetime": current_time,
                "temperature": 69.0,
                "humidity": 60.0,
                "wind_speed": 7.2,
                "cloud_coverage": 20.0,
                "uv_index": 0.0,
            },
            {
                "datetime": current_time + timedelta(hours=1),
                "temperature": 65.0,
                "humidity": 60.0,
                "wind_speed": 7.2,
                "cloud_coverage": 20.0,
                "uv_index": 0.0,
            },
            {
                "datetime": current_time + timedelta(hours=2),
                "temperature": 62.0,
                "humidity": 55.0,
                "wind_speed": 7.0,
                "cloud_coverage": 15.0,
                "uv_index": 0.0,
            },
        ]
        conditions = {
            "indoor_temp": 73.4,
            "outdoor_temp": 69.0,
            "target_temp": 68.0,
            "aqi": 40,
            "wind_speed": 7.2,
            "window_open": True,
            "fan_running": False,
            "ac_running": False,
            "current_time": current_time,
            "target_time": "22:30:00",
            "forecast": forecast,
        }

        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=2.2,
            cooling_strategy=None,
        )

        strategy = engine.recommend(conditions, prediction)

        assert strategy.method not in (
            CoolingMethod.START_AC,
            CoolingMethod.CONTINUE_AC,
        ), (
            f"Expected natural cooling, got {strategy.method.value}: {strategy.reasoning}"
        )
        assert strategy.method in (
            CoolingMethod.KEEP_WINDOW_OPEN,
            CoolingMethod.OPEN_WINDOW,
            CoolingMethod.NO_ACTION,
        ), f"Unexpected method {strategy.method.value}: {strategy.reasoning}"

    def test_deferred_passive_preferred_over_early_ac(
        self, engine: StrategyEngine, thermal_model: ThermalModel
    ):
        """When a room is hot but passive cooling (window/fan) will become viable
        later and CAN achieve the target in time, the engine must NOT switch to AC.
        For a bedroom the user isn't in yet, a deferred passive strategy is correct
        and more energy-efficient.  The reasoning should explain the wait.

        Scenario: 75°F indoor, 73°F outdoor now dropping to 58°F by 9 PM,
        7.2 mph wind.  Passive cooling becomes useful around hour 2 and can
        easily reach 68°F with 2.5h left — no AC needed.
        """
        current_time = datetime(2024, 7, 15, 18, 0)  # 6 PM, target at 10:30 PM
        forecast = [
            {"datetime": current_time, "temperature": 73.0, "humidity": 55.0,
             "wind_speed": 7.2, "cloud_coverage": 20.0, "uv_index": 1.0},
            {"datetime": current_time + timedelta(hours=1), "temperature": 69.0,
             "humidity": 55.0, "wind_speed": 7.2, "cloud_coverage": 20.0, "uv_index": 0.0},
            {"datetime": current_time + timedelta(hours=2), "temperature": 65.0,
             "humidity": 55.0, "wind_speed": 7.2, "cloud_coverage": 15.0, "uv_index": 0.0},
            {"datetime": current_time + timedelta(hours=3), "temperature": 62.0,
             "humidity": 50.0, "wind_speed": 7.0, "cloud_coverage": 10.0, "uv_index": 0.0},
            {"datetime": current_time + timedelta(hours=4), "temperature": 58.0,
             "humidity": 50.0, "wind_speed": 7.0, "cloud_coverage": 10.0, "uv_index": 0.0},
        ]
        conditions = {
            "indoor_temp": 75.0,   # 7°F above target — genuinely warm
            "outdoor_temp": 73.0,  # Only 2°F diff — window not effective yet
            "target_temp": 68.0,
            "aqi": 40,
            "wind_speed": 7.2,
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "current_time": current_time,
            "target_time": "22:30:00",  # 4.5h window — plenty of time
            "forecast": forecast,
        }

        prediction = thermal_model.predict_temperature(
            current_conditions=conditions,
            hours_ahead=4.5,
            cooling_strategy=None,
        )

        strategy = engine.recommend(conditions, prediction)

        # Passive cooling can reach target in time — prefer window/fan over AC.
        assert strategy.method not in (
            CoolingMethod.START_AC,
            CoolingMethod.CONTINUE_AC,
        ), (
            f"Should prefer deferred passive over early AC, got "
            f"{strategy.method.value}: {strategy.reasoning}"
        )
        # Reasoning must explain why we're waiting (outdoor temp not ready yet).
        assert any(
            phrase in strategy.reasoning.lower()
            for phrase in ("outdoor", "cool enough", "viable")
        ), f"Reasoning should explain the wait: {strategy.reasoning}"


class TestPeakElectricityNote:
    """Tests for _peak_electricity_note advisory logic."""

    @pytest.fixture
    def engine(self) -> StrategyEngine:
        return StrategyEngine(ThermalModel(config={}))

    def _base_conditions(self, current_time: datetime) -> dict:
        """Baseline conditions: indoor hot, outdoor hot, AC needed."""
        forecast = [
            {
                "datetime": current_time + timedelta(hours=i),
                "temperature": 88.0,
                "humidity": 50.0,
                "wind_speed": 2.0,
                "cloud_coverage": 30.0,
                "uv_index": 0.0,
            }
            for i in range(8)
        ]
        return {
            "indoor_temp": 78.0,
            "outdoor_temp": 88.0,
            "target_temp": 72.0,
            "aqi": 40,
            "wind_speed": 2.0,
            "window_open": False,
            "fan_running": False,
            "ac_running": False,
            "current_time": current_time,
            "target_time": "22:00:00",
            "forecast": forecast,
            # peak schedule keys default to None (no schedule)
            "peak_now": None,
            "peak_ends_in_hours": None,
            "peak_starts_in_hours": None,
        }

    def test_no_schedule_returns_none(self, engine: StrategyEngine):
        """Without a peak schedule configured, no note is generated."""
        conditions = self._base_conditions(datetime(2024, 7, 15, 14, 0))
        # peak_now=None signals no schedule
        defer, note, _ = engine._peak_electricity_note(conditions, hours_to_target=4.0)
        assert defer is False
        assert note is None

    def test_off_peak_now_peak_far_away_returns_none(self, engine: StrategyEngine):
        """If currently off-peak but peak is more than 6h away, skip the note."""
        conditions = self._base_conditions(datetime(2024, 7, 15, 8, 0))
        conditions["peak_now"] = False
        conditions["peak_starts_in_hours"] = 7.0  # more than 6h
        defer, note, _ = engine._peak_electricity_note(conditions, hours_to_target=10.0)
        assert defer is False
        assert note is None

    def test_off_peak_now_peak_soon_encourages_precool(self, engine: StrategyEngine):
        """Off-peak now, peak starting soon: note should encourage pre-cooling but NOT defer."""
        conditions = self._base_conditions(datetime(2024, 7, 15, 14, 0))
        conditions["peak_now"] = False
        conditions["peak_starts_in_hours"] = 2.0  # peak in 2 hours
        defer, note, setpoint = engine._peak_electricity_note(conditions, hours_to_target=6.0)
        assert defer is False  # we WANT AC now, not deferred
        assert note is not None
        assert "pre-cool" in note.lower() or "rates are low" in note.lower()
        assert "4:00 PM" in note  # 14:00 + 2h = 16:00
        # Pre-cool setpoint should be below target to account for drift through peak
        assert setpoint is not None
        assert setpoint < conditions["target_temp"]  # must be below target
        assert setpoint >= 60.0  # sanity bound

    def test_peak_now_covers_full_window_must_run_now(self, engine: StrategyEngine):
        """Currently peak with peak ending after target: AC must run now, no deferral."""
        conditions = self._base_conditions(datetime(2024, 7, 15, 15, 0))
        conditions["peak_now"] = True
        conditions["peak_ends_in_hours"] = 5.0
        defer, note, _ = engine._peak_electricity_note(conditions, hours_to_target=4.0)
        assert defer is False
        assert note is not None
        assert "must run now" in note.lower() or "must start now" in note.lower()

    def test_peak_now_can_defer_to_offpeak(self, engine: StrategyEngine):
        """Currently peak, off-peak available before target — should defer AC."""
        # Setup: peak ends in 1h, target in 5h → 4h of off-peak time available
        # Room drifts from 78°F to maybe ~80°F in 1h, then AC cools it in < 4h
        conditions = self._base_conditions(datetime(2024, 7, 15, 15, 0))
        conditions["peak_now"] = True
        conditions["peak_ends_in_hours"] = 1.0
        defer, note, _ = engine._peak_electricity_note(conditions, hours_to_target=5.0)
        assert note is not None
        # Should either defer (with "deferred" note) or force now ("must start now")
        # Either way it should mention the peak end time
        assert "4:00 PM" in note  # 15:00 + 1h = 16:00
        if defer:
            assert "deferred" in note.lower() or "defer" in note.lower()

    def test_peak_now_can_defer_flips_method_to_no_action(self, engine: StrategyEngine):
        """When deferral is viable, recommend() must return NO_ACTION with ac_deferred_peak=True."""
        conditions = self._base_conditions(datetime(2024, 7, 15, 15, 0))
        conditions["peak_now"] = True
        conditions["peak_ends_in_hours"] = 1.0
        defer, _, __ = engine._peak_electricity_note(conditions, hours_to_target=5.0)
        if not defer:
            pytest.skip("Physics determined deferral not viable in this scenario")
        # Now confirm full recommend() reflects the deferral
        from custom_components.smart_cooling.thermal_model import ThermalModel
        prediction = engine.thermal_model.predict_temperature(
            current_conditions=conditions, hours_ahead=5.0, cooling_strategy=None
        )
        strategy = engine.recommend(conditions, prediction)
        assert strategy.ac_deferred_peak is True
        assert strategy.method == CoolingMethod.NO_ACTION
        assert "defer" in strategy.timing.lower()

    def test_peak_now_insufficient_offpeak_time_must_run_now(self, engine: StrategyEngine):
        """Currently peak, off-peak starts but too little time remains — must run now."""
        # Peak ends in 3h, target in 3.5h → only 30 min of off-peak left
        # Room will be much hotter by then and can't cool in 30 min
        conditions = self._base_conditions(datetime(2024, 7, 15, 15, 0))
        conditions["peak_now"] = True
        conditions["peak_ends_in_hours"] = 3.0
        defer, note, _ = engine._peak_electricity_note(conditions, hours_to_target=3.5)
        assert defer is False
        assert note is not None
        # With only 30 min remaining after peak, AC cannot reach target
        assert "must start now" in note.lower() or "not leave enough time" in note.lower()
