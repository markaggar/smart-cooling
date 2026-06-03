"""Tests for the LearningModule."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.smart_cooling.learning_module import LearningModule, PredictionRecord
from custom_components.smart_cooling.const import DEFAULT_PHYSICS_PARAMS


def _make_hass(tmp_path: Path) -> MagicMock:
    """Return a minimal hass mock that routes I/O to a temp directory."""
    hass = MagicMock()
    hass.config.path.side_effect = lambda *parts: str(tmp_path.joinpath(*parts))

    async def _executor(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    hass.async_add_executor_job = _executor
    return hass


def _make_record(
    predicted: float,
    actual: float | None,
    timestamp: datetime,
    target_dt: datetime | None = None,
    conditions: dict | None = None,
) -> PredictionRecord:
    return PredictionRecord(
        timestamp=timestamp.isoformat(),
        predicted_temp=predicted,
        actual_temp=actual,
        conditions=conditions or {},
        params_used=dict(DEFAULT_PHYSICS_PARAMS),
        target_datetime=target_dt.isoformat() if target_dt else None,
    )


# ---------------------------------------------------------------------------
# PredictionRecord unit tests
# ---------------------------------------------------------------------------

class TestPredictionRecord:
    def test_error_when_actual_known(self):
        rec = _make_record(72.0, 75.0, datetime(2024, 1, 1))
        assert rec.prediction_error() == pytest.approx(3.0)

    def test_negative_error(self):
        rec = _make_record(76.0, 72.0, datetime(2024, 1, 1))
        assert rec.prediction_error() == pytest.approx(-4.0)

    def test_none_when_no_actual(self):
        rec = _make_record(72.0, None, datetime(2024, 1, 1))
        assert rec.prediction_error() is None


# ---------------------------------------------------------------------------
# record_prediction deduplication
# ---------------------------------------------------------------------------

class TestRecordPrediction:
    def test_deduplication_keeps_latest(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")

        target = datetime(2024, 7, 15, 22, 30)
        now = datetime(2024, 7, 15, 20, 0)

        pred_old = MagicMock()
        pred_old.predicted_target_temp = 74.0
        pred_new = MagicMock()
        pred_new.predicted_target_temp = 73.5

        lm.record_prediction(now, {}, pred_old, target_datetime=target)
        lm.record_prediction(now + timedelta(minutes=30), {}, pred_new, target_datetime=target)

        # Only one pending prediction for this target_datetime
        assert len(lm._pending_predictions) == 1
        assert lm._pending_predictions[0].predicted_temp == pytest.approx(73.5)

    def test_different_targets_both_kept(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")

        now = datetime(2024, 7, 15, 20, 0)
        pred = MagicMock()
        pred.predicted_target_temp = 73.0

        lm.record_prediction(now, {}, pred, target_datetime=datetime(2024, 7, 15, 22, 30))
        lm.record_prediction(now, {}, pred, target_datetime=datetime(2024, 7, 16, 22, 30))

        assert len(lm._pending_predictions) == 2


# ---------------------------------------------------------------------------
# record_actual matching
# ---------------------------------------------------------------------------

class TestRecordActual:
    @pytest.mark.asyncio
    async def test_matches_within_30_minutes(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")

        target = datetime(2024, 7, 15, 22, 30)
        pending = _make_record(73.0, None, datetime(2024, 7, 15, 20, 0), target_dt=target)
        lm._pending_predictions = [pending]

        # Actual measured 10 minutes after target
        await lm.record_actual(target + timedelta(minutes=10), 74.5)

        assert len(lm._historical_records) == 1
        assert lm._historical_records[0].actual_temp == pytest.approx(74.5)
        assert len(lm._pending_predictions) == 0

    @pytest.mark.asyncio
    async def test_no_match_outside_30_minutes(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")

        target = datetime(2024, 7, 15, 22, 30)
        pending = _make_record(73.0, None, datetime(2024, 7, 15, 20, 0), target_dt=target)
        lm._pending_predictions = [pending]

        # Actual measured 2 hours after target — no match
        await lm.record_actual(target + timedelta(hours=2), 74.5)

        assert len(lm._historical_records) == 0
        assert len(lm._pending_predictions) == 1

    @pytest.mark.asyncio
    async def test_no_match_emits_debug_log(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")

        target = datetime(2024, 7, 15, 22, 30)
        pending = _make_record(73.0, None, datetime(2024, 7, 15, 20, 0), target_dt=target)
        lm._pending_predictions = [pending]

        import logging
        with patch.object(
            logging.getLogger("custom_components.smart_cooling.learning_module"),
            "debug",
        ) as mock_debug:
            await lm.record_actual(target + timedelta(hours=2), 74.5)
            # At least one debug log about no match
            assert any("no pending prediction matched" in str(call).lower() for call in mock_debug.call_args_list)


# ---------------------------------------------------------------------------
# get_confidence
# ---------------------------------------------------------------------------

class TestGetConfidence:
    def test_baseline_when_no_history(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")
        assert lm.get_confidence() == pytest.approx(0.5)

    def test_high_confidence_with_accurate_predictions(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")
        now = datetime(2024, 7, 15, 20, 0)
        # 15 accurate predictions (0.2°F error each)
        lm._historical_records = [
            _make_record(73.0, 73.2, now + timedelta(hours=i))
            for i in range(15)
        ]
        confidence = lm.get_confidence()
        assert confidence > 0.9

    def test_lower_confidence_with_large_errors(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")
        now = datetime(2024, 7, 15, 20, 0)
        lm._historical_records = [
            _make_record(73.0, 73.0 + 4.0, now + timedelta(hours=i))
            for i in range(15)
        ]
        accurate_conf = 0.97  # approximate from above
        noisy_conf = lm.get_confidence()
        assert noisy_conf < accurate_conf


# ---------------------------------------------------------------------------
# compute_parameter_updates
# ---------------------------------------------------------------------------

class TestComputeParameterUpdates:
    @pytest.mark.asyncio
    async def test_returns_none_insufficient_data(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")
        lm._historical_records = [
            _make_record(73.0, 74.0, datetime(2024, 7, 15, i, 0))
            for i in range(5)
        ]
        result = await lm.compute_parameter_updates()
        assert result is None

    @pytest.mark.asyncio
    async def test_passive_mode_raises_heat_gain_rate(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")
        now = datetime(2024, 7, 15, 20, 0)

        # 15 passive records where room ended up 2°F warmer than predicted
        lm._historical_records = [
            _make_record(
                73.0, 75.0,  # actual 2°F warmer
                now + timedelta(hours=i),
                conditions={"ac_running": False, "fan_running": False, "window_open": False},
            )
            for i in range(15)
        ]

        original_rate = DEFAULT_PHYSICS_PARAMS["base_heat_gain_rate"]
        result = await lm.compute_parameter_updates()

        assert result is not None
        assert result["base_heat_gain_rate"] > original_rate

    @pytest.mark.asyncio
    async def test_no_update_when_error_below_threshold(self, tmp_path: Path):
        hass = _make_hass(tmp_path)
        lm = LearningModule(hass, "test_entry")
        now = datetime(2024, 7, 15, 20, 0)

        # 15 passive records with tiny error (0.3°F — below 0.5 threshold)
        lm._historical_records = [
            _make_record(
                73.0, 73.3,
                now + timedelta(hours=i),
                conditions={"ac_running": False, "fan_running": False, "window_open": False},
            )
            for i in range(15)
        ]

        result = await lm.compute_parameter_updates()
        assert result is None  # No change needed
