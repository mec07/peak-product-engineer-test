# Task 2: Wearable history — design

Status: draft for review. Decisions taken in chat:
- A repeated delivery replaces the earlier one.
- Only the daily summary is stored.

Task 1 (biomarker history, merged) must keep working.

## Goal

Persist one day of wearable data per delivery, from four providers with different
shapes. Then show the member's latest 30 calendar days of three metrics on the
home screen, each as a line chart with dates, values and units. All of it is served
through `GET /api/home` within the 35 ms p95 budget.

| Metric | Daily value | Unit shown |
|---|---|---|
| Resting heart rate | beats per minute | `bpm` |
| Steps | total steps | `steps` |
| Sleep efficiency | percentage | `%` |

## Data facts that drive the design

| Member | Provider | Days | Gaps | Quirks |
|---|---|---|---|---|
| user-1 | oura | 90 (2026-06-03 → 08-31) | none | — |
| user-2 | whoop | 79 of 90 | 11 missing days | different field names; no steps; efficiency is a fraction (0.749); `+01:00` timestamps |
| user-3 | garmin | 90 + 2 `_r2` | none | `_r2` changes only `resting_hr_bpm` (64→67, 58→61) |
| user-4 | fitbit | 41 (from 2026-07-22) | none | `sleep_data: null` on 14 days |

- Oura, Garmin and Fitbit share one shape:
  - `heart_rate_data.resting_hr_bpm`
  - `activity_data.steps_samples[]`: 96 fifteen-minute samples of `{timestamp, value}`
  - `sleep_data.efficiency_pct`
- Whoop's shape:
  - `heart_rate.resting.bpm`
  - no steps at all
  - `sleep.efficiency` as a 0–1 fraction
- A delivery is ~80 KB, mostly 1,440 per-minute HR samples. None of that may reach
  the home read path.
- `calendar_date` is the provider's day for the member. We use it as-is, with no
  timezone conversion.
- The replay script posts in filename order, so `2026-07-17.json` arrives before
  `2026-07-17_r2.json`.

## Storage layout

One normalised summary document per member per day:

| Partition key | Sort key | Model |
|---|---|---|
| `wearables:{user_id}` | `YYYY-MM-DD` (`calendar_date`) | `WearableDayDocument` |

```python
class WearableDayDocument(BaseModel):
    """pk=f"wearables:{user_id}", sk=date (YYYY-MM-DD)"""

    user_id: str
    date: str
    provider: str
    upload_id: str
    received_at: str  # ISO timestamp, UTC, set by the server
    resting_hr_bpm: float | None
    steps: int | None
    sleep_efficiency_pct: float | None
    superseded_upload_ids: list[str] = []
```

What it makes **cheap**:
- A member's latest 30 days. `keys()` returns sort keys without decoding, and the
  last one is the latest date. Then one `query(start=latest − 29 days)` decodes at
  most 30 documents of ~250 bytes.
- One day for ingest: a `get(pk, date)`.
- Member isolation, because each member has their own partition.

What it makes **expensive or impossible**:
- Anything across members (needs `scan`).
- Anything intra-day (raw samples aren't kept).
- Re-deriving a metric differently later (providers would have to resend).
- `keys()` grows with history: about 365 short strings a year, still cheap. If it
  ever mattered, a per-member "latest date" pointer document written at ingest
  would replace it.

## Ingest: `POST /api/users/{user_id}/wearables`

Auth stays unchanged (unauthenticated, as briefed). The existing `reference_id`
check stays.

### Normalisation — new module `app/wearables.py`

Pure functions, one adapter per provider:

```python
METRICS = ("resting_hr_bpm", "steps", "sleep_efficiency_pct")

SUPPORTED: dict[str, set[str]] = {
    "oura": set(METRICS),
    "garmin": set(METRICS),
    "fitbit": set(METRICS),
    "whoop": {"resting_hr_bpm", "sleep_efficiency_pct"},
}


def normalise(provider: str, data: dict) -> dict[str, float | int | None]:
    """The three daily metrics from one provider day; None where absent."""
```

- **Oura, Garmin, Fitbit:**
  - `resting_hr_bpm` = `heart_rate_data.resting_hr_bpm`
  - `steps` = sum of the non-null `steps_samples[].value`, or `None` if there are
    no samples or every sample is null
  - `sleep_efficiency_pct` = `sleep_data.efficiency_pct`, or `None` when
    `sleep_data` is null
- **Whoop:**
  - `resting_hr_bpm` = `heart_rate.resting.bpm`
  - `steps` = `None` (not supplied)
  - `sleep_efficiency_pct` = `round(sleep.efficiency × 100, 1)`
- Every path is read defensively (`.get` chains). A missing or null field becomes
  `None`, never a 500.
- An unknown provider returns **400** `"Unsupported provider: {name}"`, and
  nothing is stored.

### Repeated deliveries: replace

Let `existing = store.get(pk, date)`.

| Case | Action | Response |
|---|---|---|
| No existing day | write new doc | `stored: true, replaced_upload_id: null` |
| Same `upload_id` as existing | no write (idempotent) | `stored: true, replaced_upload_id: null` |
| `upload_id` in `existing.superseded_upload_ids` (stale replay) | no write | `stored: false, replaced_upload_id: null` |
| Different, new `upload_id` | write new doc; `superseded = existing.superseded + [existing.upload_id]` | `stored: true, replaced_upload_id: <old>` |

`WearablePayload` gains `date: str` and `replaced_upload_id: str | None`.

## `GET /api/home`: add a `wearables` block

`HomePayload` gains `wearables: WearableHistory | None` (`None` when the member
has no wearable days). The biomarker fields are unchanged.

```python
class DailyPoint(BaseModel):
    date: str  # YYYY-MM-DD
    value: float | None  # None: no delivery that day, or the field was null


class WearableSeries(BaseModel):
    metric: str  # "resting_hr_bpm" | "steps" | "sleep_efficiency_pct"
    label: str  # "Resting heart rate" | "Steps" | "Sleep efficiency"
    unit: str  # "bpm" | "steps" | "%"
    supported: bool  # does the member's provider supply this metric at all
    points: list[DailyPoint]  # exactly 30, oldest first, one per calendar day


class WearableHistory(BaseModel):
    provider: str  # provider of the latest day
    start: str  # end − 29 days
    end: str  # member's latest wearable date
    series: list[WearableSeries]  # always the three metrics, in table order
```

- **Window:** `end` is the member's latest stored date, not today. There are 30
  calendar days, and days with no document get `value: None`.
- **Cost:** 2 extra store calls (`keys`, `query`), at most 30 small documents.
  Expected well under 2 ms.

## Client

New section `#wearables` in `index.html`, **above** the biomarker list:
- Heading "Last 30 days".
- Subline "From {Provider} · {start} – {end}".
- Three `md-elevated-card` cards in table order, each with its chart always
  visible. The data is already in `/api/home`, so there's nothing to load lazily.

Each card:
- **Header:** label and latest non-null value with its unit, e.g. "Resting heart
  rate 58 bpm".
- **Chart:** `lineChart(canvas, {points, unit})` with no periods, so no bands.
  Labels are short dates ("3 Aug").
- **Missing values** show as **gaps in the line**. Chart.js `spanGaps: false` is
  already set in `lineChart`. When a card has gaps, a note below it says "No data
  on {n} of 30 days".
- **`supported: false`:** no chart, just "{Label} isn't available from
  {Provider}." (e.g. Whoop steps).
- **Supported but every point null:** "No {label} recorded in the last 30 days."
- **Tooltip:** date, value and unit. `lineChart` formats numbers with
  `toLocaleString()`, so steps read "21,480 steps".

When the member has no wearable data at all (`wearables: null`), the section
shows: "No wearable data yet. Connect a device to see your last 30 days."

The sign-out clearing from Task 1 extends to `#wearables`.

## Tests (`tests/test_wearables.py`, HTTP only, as its docstring asks)

1. Remove the `xfail`. A delivery returns `stored: true`.
2. **Oura mapping:** after ingesting `user-1/2026-08-31`, home shows that day's
   resting HR, the sum of the step samples, and `efficiency_pct`.
3. **Whoop mapping:** steps is `supported: false` with all-null points. Sleep is
   `efficiency × 100`.
4. **Fitbit null `sleep_data`:** that day's sleep point is `None`, and resting HR
   and steps are present.
5. **Unknown provider:** 400, and home shows no wearables.
6. **Correction replaces:** ingest Garmin `2026-07-17` then `2026-07-17_r2`. Home
   shows 67 and the response has `replaced_upload_id`.
7. **Stale replay ignored:** re-post the original after `_r2`. The response has
   `stored: false`, and home still shows 67.
8. **Idempotent resend:** the same file twice gives `stored: true` both times,
   with no change.
9. **Window:** ingest Whoop days spanning a known gap. `end` is the latest
   ingested date, `start = end − 29`, there are exactly 30 points, and the missing
   day is `None`.
10. **Isolation:** user-1's ingest never appears in user-2's home.
11. **No wearables:** `wearables` is `None`, and biomarker `results` are unchanged.

Unit tests for `normalise()` live in `tests/test_wearable_normalise.py`. They
cover each provider, null samples, and missing keys.

## Performance and verification

- Run bootstrap, then the 4 ingest commands, then `scripts/bench.py` for all 4
  emails. Record the p95 values in NOTES.md.
- Check the store log for one `/api/home`: exactly one `keys` and one `query` on
  `wearables:{id}`, and no `scan`.
- Headless screenshot check of the section for Oura (complete), Whoop (gaps and
  no steps) and Fitbit (sleep gaps).

## Out of scope

- Storing raw samples, and intra-day charts.
- Timezone normalisation beyond the provider's `calendar_date`.
- Validating `member.email` against the member record. Only `reference_id` is
  checked, as in the shell.
- Members who switch provider mid-window: `provider` and `supported` come from the
  latest day.
- Auth on ingest.

## Assumptions to record in NOTES.md

- `calendar_date` is the member's local day, as the provider defines it.
- "Later" means later arrival. A replayed original can't undo its correction,
  because superseded IDs are remembered.
- Steps are the sum of the 15-minute samples. Null samples are skipped, and if
  every sample is null the day has no value.
- Whoop's efficiency is a fraction and is converted to a percentage.
