"""Provider payloads → the three daily metrics. Pure functions, no store."""

import json
from pathlib import Path

import pytest

from app.wearables import UnsupportedProvider, normalise

WEARABLES = Path(__file__).resolve().parents[1] / "data" / "wearables"


def data(user_id: str, name: str) -> dict:
    return json.loads((WEARABLES / user_id / f"{name}.json").read_text())["data"]


def test_oura_day():
    day = data("user-1", "2026-08-31")
    metrics = normalise("oura", day)
    assert metrics.resting_hr_bpm == day["heart_rate_data"]["resting_hr_bpm"]
    assert metrics.steps == sum(
        s["value"] for s in day["activity_data"]["steps_samples"]
    )
    assert metrics.sleep_efficiency_pct == day["sleep_data"]["efficiency_pct"]


def test_whoop_day_has_no_steps_and_sleep_as_a_percentage():
    day = data("user-2", "2026-06-03")
    metrics = normalise("whoop", day)
    assert metrics.resting_hr_bpm == day["heart_rate"]["resting"]["bpm"]
    assert metrics.steps is None
    assert metrics.sleep_efficiency_pct == round(day["sleep"]["efficiency"] * 100, 1)


def test_null_sleep_data_gives_no_sleep_value():
    day = {
        "heart_rate_data": {"resting_hr_bpm": 58},
        "activity_data": {"steps_samples": [{"value": 10}]},
        "sleep_data": None,
    }
    metrics = normalise("fitbit", day)
    assert metrics.sleep_efficiency_pct is None
    assert metrics.resting_hr_bpm == 58
    assert metrics.steps == 10


def test_null_step_samples_are_skipped_and_all_null_means_no_value():
    some = {"activity_data": {"steps_samples": [{"value": 5}, {"value": None}]}}
    none = {"activity_data": {"steps_samples": [{"value": None}, {"value": None}]}}
    assert normalise("garmin", some).steps == 5
    assert normalise("garmin", none).steps is None


def test_missing_sections_give_none_not_an_error():
    metrics = normalise("oura", {})
    assert metrics.model_dump() == {
        "resting_hr_bpm": None,
        "steps": None,
        "sleep_efficiency_pct": None,
    }


def test_non_numeric_values_become_none():
    day = {
        "heart_rate_data": {"resting_hr_bpm": "54"},
        "activity_data": {"steps_samples": [{"value": True}]},
        "sleep_data": {"efficiency_pct": "high"},
    }
    assert normalise("oura", day).model_dump() == {
        "resting_hr_bpm": None,
        "steps": None,
        "sleep_efficiency_pct": None,
    }


def test_unknown_provider_raises():
    with pytest.raises(UnsupportedProvider):
        normalise("polar", {})
