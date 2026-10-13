"""One provider day → the three daily metrics we store.

Providers name and shape things differently, and not all of them supply every
metric. Each adapter reads defensively: a missing, null or non-numeric field
becomes None rather than an error, so a partial delivery still stores what it has.
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

METRICS = ("resting_hr_bpm", "steps", "sleep_efficiency_pct")

LABELS: dict[str, tuple[str, str]] = {
    "resting_hr_bpm": ("Resting heart rate", "bpm"),
    "steps": ("Steps", "steps"),
    "sleep_efficiency_pct": ("Sleep efficiency", "%"),
}


class DailyMetrics(BaseModel):
    resting_hr_bpm: float | None = None
    steps: int | None = None
    sleep_efficiency_pct: float | None = None


class UnsupportedProvider(ValueError):
    pass


def _dig(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _number(value: Any) -> float | None:
    # bool is an int subclass; a provider sending true is not sending a number.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _standard(data: dict) -> DailyMetrics:
    """Oura, Garmin and Fitbit share one shape."""
    samples = _dig(data, "activity_data", "steps_samples")
    counted = [
        n
        for s in (samples if isinstance(samples, list) else [])
        if (n := _number(_dig(s, "value"))) is not None
    ]
    return DailyMetrics(
        resting_hr_bpm=_number(_dig(data, "heart_rate_data", "resting_hr_bpm")),
        steps=int(sum(counted)) if counted else None,
        sleep_efficiency_pct=_number(_dig(data, "sleep_data", "efficiency_pct")),
    )


def _whoop(data: dict) -> DailyMetrics:
    """Whoop supplies no steps, and sleep efficiency as a 0–1 fraction."""
    efficiency = _number(_dig(data, "sleep", "efficiency"))
    return DailyMetrics(
        resting_hr_bpm=_number(_dig(data, "heart_rate", "resting", "bpm")),
        steps=None,
        sleep_efficiency_pct=(
            round(efficiency * 100, 1) if efficiency is not None else None
        ),
    )


ADAPTERS: dict[str, Callable[[dict], DailyMetrics]] = {
    "oura": _standard,
    "garmin": _standard,
    "fitbit": _standard,
    "whoop": _whoop,
}

SUPPORTED: dict[str, set[str]] = {
    "oura": set(METRICS),
    "garmin": set(METRICS),
    "fitbit": set(METRICS),
    "whoop": {"resting_hr_bpm", "sleep_efficiency_pct"},
}


def normalise(provider: str, data: dict) -> DailyMetrics:
    adapter = ADAPTERS.get(provider)
    if adapter is None:
        raise UnsupportedProvider(provider)
    return adapter(data)
