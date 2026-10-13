# Task 1: Biomarker history — design

Status: approved in chat (layout: expand per card). Task 2 (wearables) builds on this.

## Goal

From the home screen, a member can open any biomarker and see its full history as a
chart: every draw in date order, values they can inspect, and the reference ranges
that applied at each draw. The card keeps showing the latest value and unit.

## Data facts that drive the design

- 2–6 draws per member in `results:{user_id}`, sort key = ISO timestamp.
- Middle draws are partial (5–12 of 30 markers), so history length varies per marker.
- Stored ranges change between draws for 2–6 markers per member. Each point must
  use its own draw's ranges and status, not the current definition.
- Units never change within a member's history in the fixtures.

## API: extend `GET /api/home`

No new endpoint; the client still makes one request.

Store access for the biomarker part of `/api/home`, two calls total:

1. `store.query("results:{user_id}", ResultsDocument)` — every draw, oldest first.
2. `store.query("biomarker", BiomarkerDefinition)` — definitions (unchanged).

This replaces today's `keys()` + `get(last)`. Reading ≤6 small documents costs about
the same as reading one. No `scan` anywhere on this path.

### Response shape

New model, added to `app/models.py`:

```python
class HistoryPoint(BaseModel):
    tested_at: str
    value: float
    unit: str
    status: str
    ranges: Ranges
```

`BiomarkerView` gains `history: list[HistoryPoint]`, oldest first. The latest point is
also the one shown in the card. All existing `BiomarkerView` fields keep their
meaning, so existing clients and tests are unaffected. `meta` is unchanged:
`meta.tested_at` stays the latest draw's timestamp.

### Rules

- **Which markers are listed:** every marker that appears in any draw (union).
  Today a marker missing from the latest draw disappears. Each view's `value`,
  `unit`, `status`, `ranges` and `tested_at` come from that marker's most recent
  point, so a marker last measured in an earlier draw shows that draw's date.
- **Order of history:** chronological by `tested_at`. This is the store's sort-key
  order, so no re-sorting is needed.
- **Unit guard:** points whose unit differs from the latest point's unit are left out
  of `history`. We don't convert. The fixtures have no such points; the guard
  stops a chart from plotting mg/dL and mmol/L on one axis.
- **Ranges:** each point carries the ranges stored with its draw.
- Sorting of views (category, then name) is unchanged.

## Client

### New file `app/static/charts.js` (loaded before `app.js`)

One reusable function, which Task 2's wearable charts will also use:

```js
lineChart(canvas, {
  points: [{ label: "1 Oct 2024", value: 125, ranges?: {...}, status?: "optimal" }],
  unit: "mg/dL",
  bands: true | false,   // draw per-point reference bands behind the line
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
    split into two regions (say, good above and below optimal) needs no special
    case. In today's data each band is one contiguous region.
  - Any y-range not covered by a band is shaded as improve. This matches
    `band_for` in `ingest.py`, which classifies out-of-band values as `improve`.
    Example: BUN below 10 on an `in_range` marker, so a marker like that shows red
    above and below its bands.
  - Bands are drawn per point slice of the x axis. When ranges change between
    draws, the bands visibly step.
  - Colour is never the only signal. The tooltip names the band in words.
- **Y axis:** fits the data, padded so the nearest band edges are visible. It does
  not cover the full band span, which can be 0–400 and would flatten the line.
- **Tooltip:** date, value with unit, and band label ("Optimal", "Good", "Needs
  work") when a status is present.
- **Points:** coloured by status.

### Changes to `app/static/app.js`

- `renderMarker` adds a `History` button (`aria-expanded`, `aria-controls`) under the
  scale, plus a hidden chart container.
- On the first click, the chart is created from `result.history`. Later clicks only
  toggle visibility. Nothing chart-related is built on page load.
- If the history has 1 point, the panel shows "Only one result so far. Your trend
  appears after your next test." instead of a chart.
- The card header (name, latest value, unit, status) is unchanged.

### Changes to `index.html` and `styles.css`

- Add `<script src="/static/charts.js">` between `perf.js` and `app.js`.
- Add styles for the toggle button and a fixed-height chart container (~180px).

## Tests (`tests/test_home.py`)

Using the existing `home_data` fixture style, with direct `store.put` calls:

1. History is chronological and its last point matches the card's value and
   `tested_at`.
2. Each point keeps its own draw's ranges and status. Example: draw 1 has different
   ranges from draw 2, and the history shows both.
3. A partial draw shortens history only for the markers it omits.
4. A marker absent from the latest draw is still listed, with its last value and
   date.
5. A point with a mismatched unit is left out of history.
6. Member isolation: user-2's draws never appear in user-1's history.
7. The existing tests pass unchanged.

## Performance

- Expected `/api/home` cost: 2 store calls, ≤6 result documents decoded, response
  around 30 markers × ≤6 points.
- Verify with `scripts/bench.py` for all four member emails after implementing,
  and record the p95 numbers in NOTES.md.

## How this sets up Task 2

- The chart function already takes a plain `{points, unit}` series with gaps.
  Wearables need no new chart code, only `bands: false`.
- `/api/home` keeps its "one request, few store calls" shape. Task 2 adds a
  `wearables` block read from its own partition with a bounded range query.
- Task 2's storage layout is decided in its own spec, not here.

## Out of scope

- Unit conversion between mg/dL and mmol/L.
- A time-proportional x axis (it would need a date adapter).
- Changes to ingest, auth or the store.

## Assumptions to record in NOTES.md

- Each point's ranges and status are as recorded at draw time. Today's definition
  may differ.
- Markers are listed even if missing from the latest draw.
- Points with a unit that doesn't match the latest are left out of history, not
  converted.
- Equal x-spacing between draws is acceptable at this data density.
