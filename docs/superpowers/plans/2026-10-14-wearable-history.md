# Wearable History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- Persist daily wearable deliveries from Oura, Garmin, Fitbit and Whoop as a normalised summary.
- Show the member's latest 30 calendar days of resting HR, steps and sleep efficiency as line charts on the home screen, served by `/api/home` within 35 ms p95.

**Architecture:**
- `app/wearables.py` normalises each provider's payload into three metrics.
- Ingest writes one `WearableDayDocument` per member per day to `wearables:{user_id}` / `YYYY-MM-DD`. A later delivery replaces the earlier one, and superseded upload IDs are remembered.
- `/api/home` gains a `wearables` block: `keys()` finds the latest date, then one range `query` reads the 30-day window, which is gap-filled with nulls.
- The client renders three always-visible chart cards using Task 1's `lineChart()`.

**Tech Stack:** FastAPI + Pydantic v2, project `Store` (public API only), plain JS + Chart.js 4.5.1 + Material Web (labs card), pytest (`scripts/test`), ruff + ty (`scripts/lint`), Playwright 1.58 (headless checks, via `uv run --with`).

**Spec:** `docs/superpowers/specs/2026-10-14-wearable-history-design.md`

## Global Constraints

- Work on branch `task-2-wearables`. **Never push to or merge into `main`.** The final task pushes the branch and opens **one PR** into `main`. Any later amendments to Task 2 are new commits on the same branch and PR.
- Preserve the store's public API. No SQL, no `scan` on request paths. Do not edit `tests/test_store_api.py` or `app/auth.py`. Ingest stays unauthenticated.
- `/api/home` stays at or under 35 ms p95 (`scripts/bench.py`) for all four members after importing the fixtures.
- Storage: pk `wearables:{user_id}`, sk = `calendar_date` as `YYYY-MM-DD`. Only the normalised summary is stored, never raw samples.
- Window: **30** calendar days ending on the member's latest stored date (`WEARABLE_DAYS = 30`).
- Metrics, in this order, with labels and units:
  - `resting_hr_bpm` → "Resting heart rate", `bpm`
  - `steps` → "Steps", `steps`
  - `sleep_efficiency_pct` → "Sleep efficiency", `%`
- Repeats: a different new `upload_id` replaces the day. The current `upload_id` again is a no-op (`stored: true`). A superseded `upload_id` is ignored (`stored: false`).
- Unknown provider → HTTP 400, detail `"Unsupported provider: {name}"`.
- Client copy, verbatim:
  - "Last 30 days"
  - "From {Provider} · {start} – {end}"
  - "No data on {n} of 30 days."
  - "{Label} data isn't available from {Provider}."
  - "No {label lowercased} recorded in the last 30 days."
  - "No wearable data yet. Connect a device to see your last 30 days."
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. After running anything via `uv`, run `git checkout -- uv.lock`, because uv rewrites it.

## Review Focus

1. **A day older than the window** is ingested after newer days (late delivery for 40 days ago). It must not appear in the 30 points, and `end` must not move backwards. Covered by `test_window_is_thirty_days_ending_on_latest_delivery` in Task 3, which ingests an older day too.
2. **A provider sends a metric as a string or bool** (`"54"`, `true`). It must become `None`, not crash or store junk. Covered by `test_non_numeric_values_become_none` in Task 1.
3. **The member switches device and then signs out mid-session.** The next member must not see the previous wearable cards. `showSignIn` clears `#wearables` (Task 4, step 5 headless check).
4. **A chart is created before its card is attached.** Chart.js must not render at 0 px. Task 4 appends each card before calling `lineChart` (Task 4, step 5 screenshot).
5. **Every value of a supported metric is null in the window** (Fitbit sleep on a bad fortnight). The member must see the "No … recorded" copy, not an empty axis. `renderWearableCard` branch, checked in Task 4, step 5 with a mocked response.

---

### Task 1: Provider normaliser

**Files:**
- Create: `app/wearables.py`
- Test: `tests/test_wearable_normalise.py` (new)

**Interfaces:**
- Produces:
  - `METRICS: tuple[str, ...] = ("resting_hr_bpm", "steps", "sleep_efficiency_pct")`
  - `LABELS: dict[str, tuple[str, str]]` (metric → (label, unit))
  - `SUPPORTED: dict[str, set[str]]` (provider → metrics it supplies)
  - `class DailyMetrics(BaseModel)` with `resting_hr_bpm: float | None`, `steps: int | None`, `sleep_efficiency_pct: float | None`
  - `class UnsupportedProvider(ValueError)`
  - `normalise(provider: str, data: dict) -> DailyMetrics`, which raises `UnsupportedProvider` for an unknown provider

- [ ] **Step 1: Write the failing tests**

Create `tests/test_wearable_normalise.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_wearable_normalise.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'app.wearables'`.

- [ ] **Step 3: Write `app/wearables.py`**

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_wearable_normalise.py -q`
Expected: 7 passed.

- [ ] **Step 5: Lint and commit**

Run: `scripts/lint` (if it reports formatting, run `scripts/fmt` and re-run). Then `git checkout -- uv.lock`.

```bash
git add app/wearables.py tests/test_wearable_normalise.py
git commit -m "Add provider normaliser for wearable deliveries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Persist deliveries (replace on repeat)

**Files:**
- Modify: `app/models.py`. Add `WearableDayDocument` after `ResultsDocument`, and extend `WearablePayload`.
- Modify: `app/routes/ingest.py:add_wearable_day`
- Test: `tests/test_wearables.py`

**Interfaces:**
- Consumes: `normalise`, `UnsupportedProvider` (Task 1)
- Produces:
  - Document `WearableDayDocument` at pk `wearables:{user_id}`, sk `YYYY-MM-DD`
  - Response `WearablePayload {stored: bool, date: str, replaced_upload_id: str | None}`

- [ ] **Step 1: Write the failing tests**

In `tests/test_wearables.py`:
1. Delete the line `@pytest.mark.xfail(strict=True, reason="task 2: persistence not implemented")`.
2. Add these after the `sign_in` helper:

```python
@pytest.fixture(autouse=True)
def no_wearables(store):
    """dev.db may already hold imported deliveries; every test starts empty."""
    for user_id in EMAILS:
        partition = f"wearables:{user_id}"
        for key in list(store.keys(partition)):
            store.delete(partition, key)


def ingest(client, user_id, name):
    response = client.post(
        f"/api/users/{user_id}/wearables", json=delivery(user_id, name)
    )
    assert response.status_code == 200
    return response.json()["data"]
```

3. Append these tests to the end of the file:

```python
def test_unknown_provider_is_rejected(client):
    body = delivery("user-1", "2026-08-31")
    body["provider"] = "polar"
    response = client.post("/api/users/user-1/wearables", json=body)
    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported provider: polar"


def test_a_correction_replaces_the_earlier_delivery(client):
    first = ingest(client, "user-3", "2026-07-17")
    assert first == {
        "stored": True,
        "date": "2026-07-17",
        "replaced_upload_id": None,
    }
    second = ingest(client, "user-3", "2026-07-17_r2")
    assert second == {
        "stored": True,
        "date": "2026-07-17",
        "replaced_upload_id": "ga_20260717",
    }


def test_a_replayed_original_after_its_correction_is_ignored(client):
    ingest(client, "user-3", "2026-07-17")
    ingest(client, "user-3", "2026-07-17_r2")
    assert ingest(client, "user-3", "2026-07-17")["stored"] is False


def test_resending_the_same_delivery_is_a_no_op(client):
    assert ingest(client, "user-1", "2026-08-31")["stored"] is True
    again = ingest(client, "user-1", "2026-08-31")
    assert again == {"stored": True, "date": "2026-08-31", "replaced_upload_id": None}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_wearables.py -q`
Expected:
- `test_ingest_stores_a_delivery` FAILS (`stored` is False).
- The unknown-provider test FAILS (200 ≠ 400).
- The correction and resend tests FAIL (the payload lacks `date`, and `stored` is False).
- `test_home_after_ingest` passes.

- [ ] **Step 3: Add the models**

In `app/models.py`, after `class ResultsDocument`:

```python
class WearableDayDocument(BaseModel):
    """pk=f"wearables:{user_id}", sk=date (YYYY-MM-DD)

    The normalised summary of one provider day. Raw samples are not kept.
    """

    user_id: str
    date: str
    provider: str
    upload_id: str
    received_at: str
    resting_hr_bpm: float | None = None
    steps: int | None = None
    sleep_efficiency_pct: float | None = None
    superseded_upload_ids: list[str] = Field(default_factory=list)
```

Replace `class WearablePayload` with:

```python
class WearablePayload(BaseModel):
    stored: bool
    date: str
    replaced_upload_id: str | None = None
```

- [ ] **Step 4: Persist in the route**

In `app/routes/ingest.py`:
1. Add the imports `from datetime import UTC, datetime` and `from ..wearables import UnsupportedProvider, normalise`.
2. Add `WearableDayDocument` to the `..models` import list.
3. Replace `add_wearable_day` with:

```python
@router.post("/wearables", response_model=Envelope[WearablePayload])
def add_wearable_day(
    body: WearableBody,
    member: Member = Depends(ingest_member),
    store: Store = Depends(get_store),
):
    """Store one provider day as a normalised summary.

    A later delivery for the same day replaces the earlier one: providers resend a
    whole corrected day under a new upload_id. The replaced id is remembered, so a
    replay of the original can't undo the correction.
    """
    if body.member.reference_id != member.id:
        raise HTTPException(status_code=400, detail="Payload belongs to another member")

    try:
        metrics = normalise(body.provider, body.data.model_dump(mode="json"))
    except UnsupportedProvider:
        raise HTTPException(
            status_code=400, detail=f"Unsupported provider: {body.provider}"
        ) from None

    partition = f"wearables:{member.id}"
    day = body.data.calendar_date.isoformat()
    upload_id = body.data.upload_id

    existing = store.get(partition, day, WearableDayDocument)
    if existing is not None and upload_id == existing.upload_id:
        return Envelope(data=WearablePayload(stored=True, date=day))
    if existing is not None and upload_id in existing.superseded_upload_ids:
        return Envelope(data=WearablePayload(stored=False, date=day))

    superseded = (
        [*existing.superseded_upload_ids, existing.upload_id] if existing else []
    )
    store.put(
        partition,
        day,
        WearableDayDocument(
            user_id=member.id,
            date=day,
            provider=body.provider,
            upload_id=upload_id,
            received_at=datetime.now(UTC).isoformat(timespec="seconds"),
            superseded_upload_ids=superseded,
            **metrics.model_dump(),
        ),
    )
    return Envelope(
        data=WearablePayload(
            stored=True,
            date=day,
            replaced_upload_id=existing.upload_id if existing else None,
        )
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_wearables.py -q`
Expected: 6 passed.

- [ ] **Step 6: Full suite, lint, commit**

Run: `scripts/test && scripts/lint`, then `git checkout -- uv.lock`.
Expected: all pass, with no xfail left.

```bash
git add app/models.py app/routes/ingest.py tests/test_wearables.py
git commit -m "Persist wearable deliveries as daily summaries; later delivery replaces

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `wearables` block in `/api/home`

**Files:**
- Modify: `app/models.py`. Add the response models before `HomePayload`, and add `wearables` to it.
- Modify: `app/routes/home.py`. Add `wearable_history()` and use it in `home()`.
- Modify: `tests/test_home.py`. The `home_data` fixture also clears `wearables:user-1`, and `test_home_without_draws` expects `"wearables": None`.
- Test: `tests/test_wearables.py`

**Interfaces:**
- Consumes: `WearableDayDocument` (Task 2); `METRICS`, `LABELS`, `SUPPORTED` (Task 1).
- Produces: `/api/home` `data.wearables` = `null` or `{provider, start, end, series: [{metric, label, unit, supported, points: [{date, value}] ×30}] ×3}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wearables.py`:

```python
def home_wearables(client, user_id):
    response = sign_in(client, user_id).get("/api/home")
    assert response.status_code == 200
    return response.json()["data"]["wearables"]


def series(wearables, metric):
    return next(s for s in wearables["series"] if s["metric"] == metric)


def value_on(wearables, metric, day):
    points = series(wearables, metric)["points"]
    return next(p["value"] for p in points if p["date"] == day)


def test_home_without_wearables_has_none(client):
    assert home_wearables(client, "user-1") is None


def test_oura_day_reaches_home_normalised(client):
    ingest(client, "user-1", "2026-08-31")
    raw = delivery("user-1", "2026-08-31")["data"]
    w = home_wearables(client, "user-1")
    assert w["provider"] == "oura"
    assert [(s["metric"], s["label"], s["unit"]) for s in w["series"]] == [
        ("resting_hr_bpm", "Resting heart rate", "bpm"),
        ("steps", "Steps", "steps"),
        ("sleep_efficiency_pct", "Sleep efficiency", "%"),
    ]
    assert (
        value_on(w, "resting_hr_bpm", "2026-08-31")
        == (raw["heart_rate_data"]["resting_hr_bpm"])
    )
    assert value_on(w, "steps", "2026-08-31") == sum(
        s["value"] for s in raw["activity_data"]["steps_samples"]
    )
    assert (
        value_on(w, "sleep_efficiency_pct", "2026-08-31")
        == (raw["sleep_data"]["efficiency_pct"])
    )


def test_whoop_steps_are_unsupported_and_sleep_is_a_percentage(client):
    ingest(client, "user-2", "2026-08-31")
    raw = delivery("user-2", "2026-08-31")["data"]
    w = home_wearables(client, "user-2")
    steps = series(w, "steps")
    assert steps["supported"] is False
    assert all(p["value"] is None for p in steps["points"])
    assert value_on(w, "sleep_efficiency_pct", "2026-08-31") == round(
        raw["sleep"]["efficiency"] * 100, 1
    )


def test_fitbit_null_sleep_is_a_gap_but_other_metrics_remain(client):
    name = next(
        p.stem
        for p in sorted((WEARABLES / "user-4").glob("*.json"))
        if json.loads(p.read_text())["data"]["sleep_data"] is None
    )
    ingest(client, "user-4", name)
    w = home_wearables(client, "user-4")
    assert value_on(w, "sleep_efficiency_pct", name) is None
    assert value_on(w, "resting_hr_bpm", name) is not None
    assert value_on(w, "steps", name) is not None


def test_correction_shows_on_home_and_survives_a_replay(client):
    ingest(client, "user-3", "2026-07-17")
    ingest(client, "user-3", "2026-07-17_r2")
    ingest(client, "user-3", "2026-07-17")
    w = home_wearables(client, "user-3")
    assert value_on(w, "resting_hr_bpm", "2026-07-17") == 67


def test_window_is_thirty_days_ending_on_latest_delivery(client):
    present = sorted(
        p.stem
        for p in (WEARABLES / "user-2").glob("*.json")
        if "2026-08-15" <= p.stem <= "2026-08-31"
    )
    for name in present:
        ingest(client, "user-2", name)
    ingest(client, "user-2", "2026-07-01")  # late, and older than the window

    w = home_wearables(client, "user-2")
    assert (w["start"], w["end"]) == ("2026-08-02", "2026-08-31")
    dates = [p["date"] for p in series(w, "resting_hr_bpm")["points"]]
    assert len(dates) == 30
    assert dates[0] == "2026-08-02"
    assert dates[-1] == "2026-08-31"
    assert "2026-07-01" not in dates
    assert value_on(w, "resting_hr_bpm", "2026-08-16") is None  # no delivery
    assert value_on(w, "resting_hr_bpm", "2026-08-10") is None  # not ingested
    assert value_on(w, "resting_hr_bpm", "2026-08-31") is not None


def test_wearables_are_isolated_per_member(client):
    ingest(client, "user-1", "2026-08-31")
    assert home_wearables(client, "user-2") is None
    assert home_wearables(client, "user-1")["provider"] == "oura"
```

In `tests/test_home.py`:
1. In the `home_data` fixture, after the loop that deletes `results:user-1` keys, add:

```python
    for key in list(store.keys("wearables:user-1")):
        store.delete("wearables:user-1", key)
```

2. In `test_home_without_draws`, change `"data": {"results": []},` to `"data": {"results": [], "wearables": None},`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_wearables.py tests/test_home.py -q`
Expected:
- The new wearable tests FAIL with `KeyError: 'wearables'`.
- `test_home_without_draws` FAILS (`wearables` key missing).
- Everything else passes.

- [ ] **Step 3: Add the response models**

In `app/models.py`, immediately **before** `class HomePayload`, so that no forward reference is needed:

```python
class DailyPoint(BaseModel):
    date: str
    value: float | None


class WearableSeries(BaseModel):
    metric: str
    label: str
    unit: str
    supported: bool
    points: list[DailyPoint]


class WearableHistory(BaseModel):
    provider: str
    start: str
    end: str
    series: list[WearableSeries]
```

Then change `class HomePayload` to:

```python
class HomePayload(BaseModel):
    results: list[BiomarkerView]
    wearables: WearableHistory | None = None
```

- [ ] **Step 4: Read the window in `home.py`**

In `app/routes/home.py`:
1. Add the import `from datetime import date, timedelta`.
2. Extend the `..models` import with `DailyPoint, WearableDayDocument, WearableHistory, WearableSeries`.
3. Add `from ..wearables import LABELS, METRICS, SUPPORTED`.
4. Add after `latest_results`:

```python
WEARABLE_DAYS = 30


def wearable_history(store: Store, user_id: str) -> WearableHistory | None:
    """The member's latest 30 calendar days of daily metrics, gaps as None.

    The window ends on the member's latest stored day, not today. keys() finds
    that day without decoding anything, then one range query reads at most 30
    small summaries. Raw samples were never stored, so nothing here is large.
    """
    partition = f"wearables:{user_id}"
    days = list(store.keys(partition))
    if not days:
        return None

    end = date.fromisoformat(days[-1])
    start = end - timedelta(days=WEARABLE_DAYS - 1)
    stored = dict(store.query(partition, WearableDayDocument, start=start.isoformat()))
    calendar = [(start + timedelta(days=i)).isoformat() for i in range(WEARABLE_DAYS)]
    provider = stored[days[-1]].provider

    series = []
    for metric in METRICS:
        label, unit = LABELS[metric]
        series.append(
            WearableSeries(
                metric=metric,
                label=label,
                unit=unit,
                supported=metric in SUPPORTED.get(provider, set()),
                points=[
                    DailyPoint(
                        date=day,
                        value=getattr(stored[day], metric) if day in stored else None,
                    )
                    for day in calendar
                ],
            )
        )
    return WearableHistory(
        provider=provider, start=calendar[0], end=calendar[-1], series=series
    )
```

5. In `home()`, change `data=HomePayload(results=results),` to:

```python
data = (HomePayload(results=results, wearables=wearable_history(store, member.id)),)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_wearables.py tests/test_home.py -q`
Expected: all pass.

- [ ] **Step 6: Full suite, lint, commit**

Run: `scripts/test && scripts/lint`, then `git checkout -- uv.lock`.

```bash
git add app/models.py app/routes/home.py tests/test_wearables.py tests/test_home.py
git commit -m "Serve a 30-day wearable window through /api/home

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: "Last 30 days" section on the home screen

**Files:**
- Modify: `app/static/index.html`. Add `<section id="wearables" class="wearables" hidden></section>` as the first child of `<main>`, before `<div id="results">`.
- Modify: `app/static/app.js`. Add `renderWearables`, `renderWearableCard` and helpers; call them from `showDashboard`; clear in `showSignIn`.
- Modify: `app/static/charts.js`. Format tooltip numbers with `toLocaleString()`.
- Modify: `app/static/styles.css`. Add wearable card spacing.

**Interfaces:**
- Consumes: `/api/home` `data.wearables` (Task 3); `lineChart(canvas, {points, unit})` and `note(text)` (Task 1 history code in `app.js`).
- Produces: nothing for later tasks.

- [ ] **Step 1: Locale-format tooltip numbers**

In `app/static/charts.js`, in the tooltip `label(item)` callback, change

```js
                return `${p.value} ${unit}${band ? ` · ${band}` : ""}`;
```

to

```js
                return `${p.value.toLocaleString()} ${unit}${band ? ` · ${band}` : ""}`;
```

- [ ] **Step 2: Render the section**

In `app/static/app.js`, add after `renderResults`:

```js
const PROVIDER_NAME = { oura: "Oura", garmin: "Garmin", fitbit: "Fitbit", whoop: "Whoop" };

function formatDay(isoDate) {
  // Date-only strings parse as UTC midnight; pin to local midnight instead so
  // the label never slips to the previous day.
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

/* Wearable history arrives with /api/home, so these charts draw immediately.
   Each card is attached before its chart is created so Chart.js measures a
   real container. */
function renderWearables(wearables) {
  const root = el("wearables");
  root.replaceChildren();
  root.hidden = false;

  const heading = document.createElement("h2");
  heading.className = "category";
  heading.textContent = "Last 30 days";
  root.append(heading);

  if (!wearables) {
    root.append(note("No wearable data yet. Connect a device to see your last 30 days."));
    return;
  }

  const provider = PROVIDER_NAME[wearables.provider] ?? wearables.provider;
  const source = document.createElement("p");
  source.className = "lede wearables-source";
  source.textContent = `From ${provider} · ${formatDay(wearables.start)} – ${formatDay(wearables.end)}`;
  root.append(source);

  for (const series of wearables.series) renderWearableCard(root, series, provider);
}

function renderWearableCard(root, series, provider) {
  const card = document.createElement("md-elevated-card");
  card.className = "marker wearable";
  root.append(card);

  const present = series.points.filter((p) => p.value != null);
  const latest = present.at(-1);

  const head = document.createElement("div");
  head.className = "marker-head wearable-head";
  const name = document.createElement("span");
  name.className = "marker-name";
  name.textContent = series.label;
  head.append(name);
  if (series.supported && latest) {
    const reading = document.createElement("span");
    const value = document.createElement("span");
    value.className = "marker-value";
    value.textContent = latest.value.toLocaleString();
    const unit = document.createElement("span");
    unit.className = "marker-unit";
    unit.textContent = series.unit;
    reading.append(value, unit);
    head.append(reading);
  }

  const body = document.createElement("div");
  body.className = "wearable-body";
  card.append(head, body);

  if (!series.supported) {
    body.append(note(`${series.label} data isn't available from ${provider}.`));
    return;
  }
  if (!present.length) {
    body.append(note(`No ${series.label.toLowerCase()} recorded in the last 30 days.`));
    return;
  }

  const wrap = document.createElement("div");
  wrap.className = "history-chart";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute(
    "aria-label",
    `${series.label}, last 30 days, ${present.length} days with data`
  );
  wrap.append(canvas);
  body.append(wrap);

  const missing = series.points.length - present.length;
  if (missing) body.append(note(`No data on ${missing} of ${series.points.length} days.`));

  lineChart(canvas, {
    unit: series.unit,
    points: series.points.map((p) => ({ label: formatDay(p.date), value: p.value })),
  });
}
```

- [ ] **Step 3: Wire it into the dashboard lifecycle**

In `showDashboard`, after `renderResults(data.results);`:

```js
  renderWearables(data.wearables);
```

In `showSignIn`, after `el("results").replaceChildren();`:

```js
  el("wearables").replaceChildren();
  el("wearables").hidden = true;
```

In `app/static/index.html`, make `<main>` read:

```html
  <main>
    <section id="wearables" class="wearables" hidden></section>
    <div id="results"></div>
  </main>
```

- [ ] **Step 4: Styles**

Append to `app/static/styles.css`:

```css
/* ---- wearables ---- */

.wearables-source { margin: 0.35rem 0 0; }

.wearable-head { padding: 1rem 1rem 0; }

.wearable-body { padding: 0.5rem 1rem 1rem; }
```

- [ ] **Step 5: Headless check (Review Focus 3, 4 and 5)**

`scripts/dev` must be running. Import the fixtures first:

```bash
scripts/bootstrap.py
for u in user-1 user-2 user-3 user-4; do scripts/ingest-wearable-data.py --user $u; done
```

Then write `$SCRATCHPAD/wearables_check.py`:

```python
import json, sys
from playwright.sync_api import sync_playwright

OUT = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 820, "height": 1300})
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))

    def sign_in(email):
        pg.fill("#email", email)
        pg.click("#signin-button")
        pg.wait_for_selector("#wearables:not([hidden]) .wearable")

    pg.goto("http://localhost:8000/")
    for user, email in [
        ("oura", "james.chen"),
        ("whoop", "priya.raman"),
        ("fitbit", "sofia.marek"),
    ]:
        sign_in(f"{email}@example.com")
        pg.wait_for_function("!!customElements.get('md-elevated-card')", timeout=15000)
        pg.wait_for_timeout(400)
        w = pg.locator("#wearables")
        print(
            user,
            "|",
            w.locator(".wearables-source").inner_text(),
            "|",
            [
                c.inner_text().replace("\n", " ")[:90]
                for c in w.locator(".wearable").all()
            ],
            "| canvas heights:",
            pg.evaluate(
                "[...document.querySelectorAll('#wearables canvas')].map(c => c.clientHeight)"
            ),
        )
        w.screenshot(path=f"{OUT}/wearables-{user}.png")
        pg.click("#signout-button")
        pg.wait_for_selector("#signin:not([hidden])")
        print(
            "  after sign-out, wearable cards:",
            pg.locator("#wearables .wearable").count(),
        )

    # all-null supported metric: mock /api/home
    def fake(route):
        resp = route.fetch()
        body = resp.json()
        for s in body["data"]["wearables"]["series"]:
            if s["metric"] == "sleep_efficiency_pct":
                for pt in s["points"]:
                    pt["value"] = None
        route.fulfill(response=resp, body=json.dumps(body))

    pg.route("**/api/home", fake)
    sign_in("james.chen@example.com")
    print(
        "all-null sleep card:",
        pg.locator(".wearable", has_text="Sleep efficiency")
        .inner_text()
        .replace("\n", " "),
    )
    print("errors:", errors)
    b.close()
```

Run: `uv run --quiet --with playwright==1.58.0 python $SCRATCHPAD/wearables_check.py $SCRATCHPAD`, then `git checkout -- uv.lock`.
Expected:
- Oura: 3 charts, source "From Oura · Aug 2 – Aug 31" (headless Chromium uses the en-US locale), canvas heights all 180.
- Whoop: the steps card shows "Steps data isn't available from Whoop.", and the other cards show "No data on N of 30 days." with N ≥ 1.
- Fitbit: the sleep card shows "No data on N of 30 days."
- After each sign-out, `wearable cards: 0`.
- The all-null sleep card shows "No sleep efficiency recorded in the last 30 days."
- `errors: []`.

Open the three PNGs and confirm visible lines with gaps.

- [ ] **Step 6: Commit**

```bash
git add app/static/index.html app/static/app.js app/static/charts.js app/static/styles.css
git commit -m "Show the last 30 days of wearable metrics on the home screen

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Verify, record, raise the PR

**Files:**
- Modify: `NOTES.md`

**Interfaces:**
- Consumes: everything above. Produces: the Task 2 PR.

- [ ] **Step 1: Suite and lint**

Run: `scripts/test && scripts/lint`, then `git checkout -- uv.lock`.
Expected: all pass, 0 xfailed.

- [ ] **Step 2: Benchmark `/api/home` with the fixtures imported**

With `scripts/dev` running and the fixtures imported (Task 4, step 5):

```bash
for e in james.chen priya.raman tom.okafor sofia.marek; do scripts/bench.py --email $e@example.com; done
```

Expected: every run reports `PASS` with p95 ≤ 35 ms. If a single run fails while the others pass, re-run that member 3 times and record all the values. The laptop's load makes bench noisy (see the Task 1 notes).

- [ ] **Step 3: Confirm the access pattern**

```bash
uv run python - <<'EOF'
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
c.post("/api/auth/login", json={"email": "tom.okafor@example.com"})
print("---- /api/home store calls ----")
c.get("/api/home")
EOF
git checkout -- uv.lock
```

Expected: after the marker line, exactly these `store:` lines, plus the session `get` from auth:
- `store:keys pk=results:user-3`
- `store:get pk=results:user-3, …`
- `store:query pk=biomarker, …`
- `store:keys pk=wearables:user-3, …`
- `store:query pk=wearables:user-3, …, start=2026-08-02, …`

There must be no `scan`.

- [ ] **Step 4: Update NOTES.md**

Under `## Task 2: Wearable history`, after the existing Decisions:

```markdown
### Storage layout

| Partition key | Sort key | Document |
|---|---|---|
| `wearables:{user_id}` | `YYYY-MM-DD` | `WearableDayDocument`: provider, upload_id, received_at, the 3 metrics (null when absent), superseded upload ids |

- **Cheap:** latest 30 days for one member. `keys()` (sort keys only, no decoding) finds the latest date, then one range `query` reads ≤30 docs of ~250 bytes. `/api/home` adds exactly 2 store calls.
- **Expensive or impossible:** anything across members (`scan`); intra-day detail (raw samples not kept); re-deriving a metric (providers must resend).
- `keys()` grows by one short string per day (~365/year). If that ever matters, a per-member "latest date" pointer written at ingest replaces it.

### Other decisions

- **Missing values are gaps in the line.** No interpolation and no zero-filling. Each chart says "No data on N of 30 days".
- **Metric not supplied by provider (Whoop steps):** the card says "Steps data isn't available from Whoop." instead of showing an empty chart.
- **Window ends on the member's latest delivery, not today**, as briefed. A late delivery for an older day never moves the window back.
- **Steps** = sum of the 15-minute samples. Null samples are skipped; all-null means no value. **Whoop sleep efficiency** is a 0–1 fraction, ×100.
- **Units:** bpm, steps, % (normalised at ingest so the client never converts).

### Verification

- [fill in: test counts, the 4 bench p95 values, store-call log excerpt]

### Left out / next

- Raw sample storage and intra-day charts.
- Timezone handling beyond the provider's `calendar_date`.
- Checking `member.email` against the member record.
- Members who switch provider mid-window: provider and `supported` come from the latest day.
```

Replace the `[fill in: …]` line with the measured numbers from steps 1–3. It's an instruction to you, not text to leave in. Also append one bullet to `## How I used AI tools` describing Task 2's spec → plan → native execution and the headless checks.

- [ ] **Step 5: Commit, push the branch, raise the PR**

```bash
git add NOTES.md
git commit -m "NOTES: Task 2 storage layout, decisions, verification

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u submission task-2-wearables
gh pr create -R mec07/peak-product-engineer-test --base main --head task-2-wearables \
  --title "Task 2: wearable ingest and 30-day history" \
  --body-file "$SCRATCHPAD/pr-task-2.md"
```

Write `$SCRATCHPAD/pr-task-2.md` first. It has a `## Summary` (storage layout, replace-on-repeat, normalisation, `/api/home` block, UI), a `## Test plan` checklist with the real numbers from steps 1–3 and the Task 4 headless check, and it ends with:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**Do not merge.** Report the PR URL. Any later Task 2 amendments are new commits pushed to `task-2-wearables`, so they land on this same PR.
