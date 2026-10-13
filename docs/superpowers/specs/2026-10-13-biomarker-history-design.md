# Task 1: Biomarker history — design

Status: approved in chat. Layout: expandable card; history is fetched when a card is
first expanded. Both chart types are limited to 30 points. Task 2 (wearables) builds on this.

## Goal

From the home screen, a member can open any biomarker and see its full history as a
chart: every draw in date order, values they can inspect, and the reference ranges
that applied at each draw. The card keeps showing the latest value and unit.

## Data facts that drive the design

- Today there are 2–6 draws per member in `results:{user_id}`, sort key = ISO
  timestamp. This will grow; see Scaling.
- Middle draws are partial (5–12 of 30 markers), so history length varies per marker.
- Stored ranges change between draws for 2–6 markers per member. Each point must
  use its own draw's ranges and status, not the current definition.
- Units never change within a member's history in the fixtures.

## API

### `GET /api/home` — unchanged

Home keeps its current reads (`keys()` + one `get` + definitions). Its cost doesn't
depend on how many draws a member has, so history growth never touches the 35 ms
budget.

### New: `GET /api/biomarkers/history`

Signed-in member only (`Depends(current_user)`), same envelope as the other routes.
It's fetched by the client the first time any card is expanded, then cached for the
page's lifetime, so expanding more cards doesn't re-fetch.

Store access: limited to the **latest 30 draws**, the same 30-point limit Task 2 uses
for wearable days.

1. `store.keys("results:{user_id}")` lists sort keys only, with no document decoding.
2. `store.query("results:{user_id}", ResultsDocument, start=keys[-30])` decodes at
   most 30 draws, oldest first.

No `scan`. Cost is capped at about 4 ms of decoding (measured at 30 draws),
however long a member's history gets. `meta.truncated` is `true` when older draws
exist, and the chart then says "Showing your last 30 tests".

Why one endpoint for all markers rather than `/biomarkers/{id}/history`: each draw
document (`results:{user_id}` / `tested_at`) holds every marker measured that day,
about 30 results in one ~7 KB document. One marker's history therefore means
reading every draw document and discarding the other 29 markers. A per-marker
endpoint would repeat that read on every expand. Reading the draws once returns
all markers for the same cost, and later expands are instant. Making per-marker
reads cheap needs a different key layout (see Scaling).

Response:

```python
class HistoryPoint(BaseModel):
    tested_at: str
    value: float
    status: str                  # as recorded at the draw

class RangePeriod(BaseModel):
    from_tested_at: str          # first point these ranges apply to
    ranges: Ranges

class BiomarkerHistory(BaseModel):
    biomarker_id: str
    unit: str
    points: list[HistoryPoint]   # oldest first
    range_periods: list[RangePeriod]  # oldest first; a new entry only when ranges change

class HistoryPayload(BaseModel):
    biomarkers: list[BiomarkerHistory]
```

`meta`: `{"draws": <number of draws read>, "truncated": <bool>}`.

### Rules

- **Order:** points are chronological by `tested_at`. This is the store's sort-key
  order, so no re-sorting is needed.
- **Partial draws:** a marker absent from a draw has no point for it. No
  interpolation.
- **Status:** each point carries the status stored with its draw. It's one short
  string, and it's what was told to the member at the time.
- **Ranges:** sent as periods, not per point. Walking points in order, a new
  `RangePeriod` starts at the first point and whenever a point's stored ranges
  differ from the previous point's. In the fixtures, 60–110 points per member
  collapse to 32–36 periods, with at most 2 per marker. Ranges are about 70% of a
  point's bytes, so this cuts the payload roughly in half to two-thirds. The bands
  are also periods, so this is the shape the chart needs.
- **Unit:** sent once per marker, not per point.
- **Unit guard:** `unit` is the latest point's unit. Earlier points with a different
  unit are left out, not converted. The fixtures have no such points; the guard
  stops a chart from plotting mg/dL and mmol/L on one axis.

## Client

### New file `app/static/charts.js` (loaded before `app.js`)

One reusable function, which Task 2's wearable charts will also use:

```js
lineChart(canvas, {
  points: [{ label: "1 Oct 2024", value: 125, status?: "optimal" }],
  unit: "mg/dL",
  periods?: [{ startIndex: 0, ranges: {...} }],  // omitted for wearables: no bands
})
```

- **X axis:** a category axis with labels already formatted as dates, so no Chart.js
  date adapter is needed. Draws are roughly evenly spaced, so even spacing doesn't
  mislead. For Task 2, the server sends one point per calendar day with
  `value: null` for missing days, and Chart.js shows those as breaks in the line
  (`spanGaps: false`).
- **Bands:** a small inline Chart.js plugin (`beforeDatasetsDraw`) colours the
  chart background by band: optimal green, good amber, improve red. It uses the
  existing `--optimal`, `--good` and `--improve` colours from `styles.css` at about
  18% opacity, so the line and points stay readable on top.
  - The plugin loops over whatever bands a point's ranges contain, so a band
    split into two regions needs no special case. In today's data each band is one
    contiguous region.
  - Any y-range not covered by a band is shaded as improve. This matches
    `band_for` in `ingest.py`, which classifies out-of-band values as `improve`.
    Example: BUN below 10 on an `in_range` marker.
  - Each `RangePeriod` is drawn from its first point's x-slice to the slice
    before the next period starts. When ranges change, the bands visibly step.
    Wearable charts pass no periods.
- **Y axis:** fits the data, padded so the nearest band edges are visible. It does
  not cover the full band span, which can be 0–400 and would flatten the line.
- **Tooltip:** date, value with unit, and band label in words ("Optimal", "Good",
  "Needs work"), so colour is never the only signal.
- **Points:** coloured by status.

### Changes to `app/static/app.js`

- `renderMarker` adds a `History` button (`aria-expanded`, `aria-controls`) under the
  scale, plus a hidden panel.
- On the first expand of any card, the client calls `/api/biomarkers/history` and
  keeps the promise in a module-level cache. All cards share it, so there's one
  request per page load at most.
- While the request is in flight, the panel shows "Loading history…". If it fails,
  the panel shows "Couldn't load history." and a retry button, which clears the
  cache.
- The chart is built on that card's first open. Later clicks only toggle
  visibility.
- If the marker has 1 point, the panel shows "Only one result so far. Your trend
  appears after your next test." instead of a chart.
- The card header (name, latest value, unit, status) is unchanged.
- On sign-out, the cache is cleared so the next member never sees the previous
  member's history.

### Changes to `index.html` and `styles.css`

- Add `<script src="/static/charts.js">` between `perf.js` and `app.js`.
- Add styles for the toggle button, the status messages and a fixed-height chart
  container (~180px).

## Tests (`tests/test_biomarker_history.py`)

Using the `home_data`-style fixtures with direct `store.put`:

1. The endpoint requires a valid session (401 without one).
2. Points are chronological, and the last point matches `/api/home`'s value and
   `tested_at` for that marker.
3. Each point keeps its own draw's status. Ranges collapse into periods: identical
   ranges give 1 period, and a change gives a new period starting at that draw.
4. A partial draw shortens history only for the markers it omits.
5. A point with a mismatched unit is left out.
6. Member isolation: user-2's draws never appear in user-1's history.
7. A member with no draws gets `biomarkers: []`.
8. With 31 draws, only the latest 30 are returned and `meta.truncated` is `true`.
9. `/api/home`'s response is unchanged, so the existing tests pass untouched.

## Scaling

Measured on a scratch DB (typed query, p50 of 20 runs):

| Draws | Read + validate | History payload |
|---|---|---|
| 6 | 0.8 ms | ~37 KB |
| 30 | 4.3 ms | ~188 KB |
| 100 | 14.8 ms | ~629 KB |

- `/api/home`: flat at any draw count, because it reads only the latest draw.
- History endpoint: capped at 30 draws, so at most about 4 ms of decoding, and under
  about 100 KB once ranges are sent as periods. It's off the budgeted path and runs once per page.
- Next step if draw counts grow (for NOTES.md, not built now): a per-marker history
  copy written at ingest, `history:{user_id}` with sk = `biomarker_id` and compact
  points. Reads become one small doc per marker. The cost moves to writes, and
  bootstrap plus draw ingest would both need to maintain it.

## How this sets up Task 2

- The chart function already takes a plain `{points, unit}` series with gaps.
  Wearables need no new chart code; they just omit `periods`.
- Task 2 requires wearable chart data inside `/api/home`. Leaving biomarker history
  out of `/api/home` keeps that budget free for the wearables block.
- Task 2's storage layout is decided in its own spec.

## Out of scope

- Unit conversion between mg/dL and mmol/L.
- A time-proportional x axis (it would need a date adapter).
- Changes to ingest, auth or the store.

## Assumptions to record in NOTES.md

- Each point's ranges and status are as recorded at draw time. Today's definition
  may differ.
- History is loaded on demand, not with the home screen. Trade-off: the first
  expand waits on one extra request.
- Points with a unit that doesn't match the latest are left out of history, not
  converted.
- Equal x-spacing between draws is acceptable at this data density.
