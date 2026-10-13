# Biomarker History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each biomarker card on the home screen gets a "History" toggle. It opens a line chart of the latest 30 draws, with colour bands for the reference ranges that applied at each draw.

**Architecture:**
- A new endpoint, `GET /api/biomarkers/history`, reads the member's `results:{user_id}` partition once (latest 30 draws) and returns, per marker: its points, its unit, and its reference ranges as periods.
- The client fetches it on the first expand of any card and caches the result. A reusable `lineChart()` in `app/static/charts.js` draws the chart; Task 2 will reuse it for wearables.
- `/api/home` is not touched.

**Tech Stack:** FastAPI + Pydantic v2, the project's `Store` (SQLite-backed, public API only), plain JS + Chart.js 4.5.1 (global `Chart`, loaded from CDN), pytest via `scripts/test`, ruff + ty via `scripts/lint`.

**Spec:** `docs/superpowers/specs/2026-10-13-biomarker-history-design.md`

## Global Constraints

- Preserve the store's public API (`get, query, keys, scan, put, put_many, delete, close`). No SQL, and no `scan` on request paths.
- Do not edit `tests/test_store_api.py` or `app/auth.py`.
- `/api/home` stays at or under 35 ms p95 (`scripts/bench.py`). Its code must not change in this plan.
- API fields use snake_case. Responses use `{"data": ..., "meta": ...}` (the `Envelope` model).
- History is limited to the latest **30** draws (`HISTORY_LIMIT = 30`).
- Band colours come from the CSS variables `--optimal`, `--good` and `--improve`, at **0.18** alpha. Any area outside every band is shaded as improve.
- Band labels: `optimal` → "Optimal", `good` → "Good", `improve` → "Needs work" (same as `BAND_LABEL` in `app.js`).
- Copy, verbatim:
  - "Loading history…"
  - "Couldn't load history."
  - "Try again"
  - "Only one result so far. Your trend appears after your next test."
  - "Showing your last 30 tests."
- No Chart.js date adapter. Use a category x-axis with preformatted date labels.

## Review Focus

1. **Member switch on one page:** member A opens a card, signs out, and member B signs in. B must not see A's history. The client cache is reset in `showDashboard` and on sign-out (Task 3, step 6 manual check).
2. **Value outside every band** (e.g. BUN 8 when the bands start at 10): the point must be visible on the y-axis and drawn on red. Task 2 `yBounds` includes the data min/max, and the plugin fills improve first (Task 2, step 3 manual check).
3. **Expand clicked again while the request is in flight, or two cards opened quickly:** there must be exactly one network request and no duplicated panel content. The shared promise plus the `dataset.rendered` guard covers this (Task 3, step 6 manual check).
4. **Exactly 30 draws:** `truncated` must be `false`, and no "Showing your last 30 tests." note appears. Covered by `test_exactly_thirty_draws_is_not_truncated` in Task 1.
5. **History request fails** (server down): the panel shows the error and a "Try again" button that retries. Closing and reopening the card also retries (Task 3, step 6 manual check).

---

### Task 1: History endpoint

**Files:**
- Modify: `app/models.py`. Add the response models after `HomePayload`.
- Create: `app/routes/history.py`
- Modify: `app/main.py`. Import and include the router.
- Test: `tests/test_biomarker_history.py` (new)

**Interfaces:**
- Produces:
  - `GET /api/biomarkers/history` → `{"data": {"biomarkers": [BiomarkerHistory...]}, "meta": {"draws": int, "truncated": bool}}`
  - `BiomarkerHistory = {biomarker_id: str, unit: str, points: [{tested_at: str, value: float, status: str}], range_periods: [{from_tested_at: str, ranges: {optimal|good|improve: {min, max}}}]}`, with points and periods oldest first and `biomarkers` sorted by `biomarker_id`.
  - Python: `biomarker_history(store: Store, user_id: str, limit: int = HISTORY_LIMIT) -> tuple[list[BiomarkerHistory], int, bool]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_biomarker_history.py`:

```python
"""Biomarker history: GET /api/biomarkers/history.

The seeded dev.db already holds draws for every member, so the fixture clears
user-1's and user-2's partitions first and each test writes exactly the draws it
needs.
"""

import pytest

from app.models import BiomarkerResult, Ranges, ResultsDocument


def ranges(optimal_max: float = 20) -> Ranges:
    return Ranges(
        optimal={"min": 10, "max": optimal_max},
        good={"min": optimal_max, "max": 25},
        improve={"min": 25, "max": 30},
    )


def result(
    marker_id: str,
    value: float,
    *,
    unit: str = "mg/dL",
    status: str = "good",
    marker_ranges: Ranges | None = None,
) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_id=marker_id,
        value=value,
        unit=unit,
        status=status,
        ranges=marker_ranges or ranges(),
    )


@pytest.fixture
def add_draw(store):
    for user_id in ("user-1", "user-2"):
        partition = f"results:{user_id}"
        for key in list(store.keys(partition)):
            store.delete(partition, key)

    def add(user_id: str, tested_at: str, *results: BiomarkerResult) -> None:
        store.put(
            f"results:{user_id}",
            tested_at,
            ResultsDocument(
                user_id=user_id, tested_at=tested_at, results=list(results)
            ),
        )

    return add


def history(client) -> dict:
    response = client.get("/api/biomarkers/history")
    assert response.status_code == 200
    return response.json()


def by_id(body: dict) -> dict[str, dict]:
    return {h["biomarker_id"]: h for h in body["data"]["biomarkers"]}


def test_history_requires_a_session(client):
    assert client.get("/api/biomarkers/history").status_code == 401


def test_member_without_draws_gets_empty_history(signed_in, add_draw):
    assert history(signed_in) == {
        "data": {"biomarkers": []},
        "meta": {"draws": 0, "truncated": False},
    }


def test_points_are_chronological_and_end_at_the_home_value(signed_in, add_draw):
    # Written out of order: the store's sort-key order is what we rely on.
    add_draw("user-1", "2026-09-01T09:00:00", result("ldl_cholesterol", 90))
    add_draw("user-1", "2025-03-01T09:00:00", result("ldl_cholesterol", 120))
    add_draw("user-1", "2025-09-01T09:00:00", result("ldl_cholesterol", 105))

    ldl = by_id(history(signed_in))["ldl_cholesterol"]
    assert [p["tested_at"] for p in ldl["points"]] == [
        "2025-03-01T09:00:00",
        "2025-09-01T09:00:00",
        "2026-09-01T09:00:00",
    ]
    assert [p["value"] for p in ldl["points"]] == [120, 105, 90]

    home = signed_in.get("/api/home").json()["data"]["results"]
    latest = next(r for r in home if r["biomarker_id"] == "ldl_cholesterol")
    assert ldl["points"][-1]["value"] == latest["value"]
    assert ldl["points"][-1]["tested_at"] == latest["tested_at"]
    assert ldl["unit"] == latest["unit"]


def test_status_is_per_point_and_ranges_collapse_into_periods(signed_in, add_draw):
    add_draw("user-1", "2025-01-01T09:00:00", result("a", 15, status="optimal"))
    add_draw("user-1", "2025-06-01T09:00:00", result("a", 21, status="good"))
    add_draw(
        "user-1",
        "2026-01-01T09:00:00",
        result("a", 21, status="optimal", marker_ranges=ranges(optimal_max=22)),
    )

    marker = by_id(history(signed_in))["a"]
    assert [p["status"] for p in marker["points"]] == ["optimal", "good", "optimal"]
    assert marker["range_periods"] == [
        {"from_tested_at": "2025-01-01T09:00:00", "ranges": ranges().model_dump()},
        {
            "from_tested_at": "2026-01-01T09:00:00",
            "ranges": ranges(optimal_max=22).model_dump(),
        },
    ]


def test_partial_draw_shortens_only_the_markers_it_omits(signed_in, add_draw):
    add_draw("user-1", "2025-01-01T09:00:00", result("a", 11), result("b", 12))
    add_draw("user-1", "2025-06-01T09:00:00", result("a", 13))
    add_draw("user-1", "2026-01-01T09:00:00", result("a", 14), result("b", 15))

    markers = by_id(history(signed_in))
    assert [p["value"] for p in markers["a"]["points"]] == [11, 13, 14]
    assert [p["value"] for p in markers["b"]["points"]] == [12, 15]


def test_points_in_a_different_unit_from_the_latest_are_left_out(signed_in, add_draw):
    add_draw("user-1", "2025-01-01T09:00:00", result("a", 2.6, unit="mmol/L"))
    add_draw("user-1", "2026-01-01T09:00:00", result("a", 100, unit="mg/dL"))

    marker = by_id(history(signed_in))["a"]
    assert marker["unit"] == "mg/dL"
    assert [p["value"] for p in marker["points"]] == [100]
    assert [p["from_tested_at"] for p in marker["range_periods"]] == [
        "2026-01-01T09:00:00"
    ]


def test_history_only_contains_the_signed_in_members_draws(client, add_draw):
    add_draw("user-1", "2026-01-01T09:00:00", result("a", 11))
    add_draw("user-2", "2026-02-01T09:00:00", result("a", 22), result("z", 5))

    for email, values, ids in (
        ("james.chen@example.com", [11], {"a"}),
        ("priya.raman@example.com", [22], {"a", "z"}),
    ):
        assert client.post("/api/auth/login", json={"email": email}).status_code == 200
        markers = by_id(history(client))
        assert set(markers) == ids
        assert [p["value"] for p in markers["a"]["points"]] == values


def test_history_is_capped_at_the_latest_thirty_draws(signed_in, add_draw):
    for day in range(1, 32):
        add_draw("user-1", f"2024-01-{day:02d}T09:00:00", result("a", day))

    body = history(signed_in)
    points = by_id(body)["a"]["points"]
    assert len(points) == 30
    assert points[0]["tested_at"] == "2024-01-02T09:00:00"
    assert points[-1]["tested_at"] == "2024-01-31T09:00:00"
    assert body["meta"] == {"draws": 30, "truncated": True}


def test_exactly_thirty_draws_is_not_truncated(signed_in, add_draw):
    for day in range(1, 31):
        add_draw("user-1", f"2024-01-{day:02d}T09:00:00", result("a", day))

    assert history(signed_in)["meta"] == {"draws": 30, "truncated": False}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_biomarker_history.py -q`
Expected: 9 failed. The route doesn't exist yet, so every request returns 404, including the one that expects 401.

- [ ] **Step 3: Add the response models**

In `app/models.py`, directly after `class HomePayload`:

```python
class HistoryPoint(BaseModel):
    tested_at: str
    value: float
    status: str


class RangePeriod(BaseModel):
    """Ranges that apply from `from_tested_at` until the next period starts."""

    from_tested_at: str
    ranges: Ranges


class BiomarkerHistory(BaseModel):
    biomarker_id: str
    unit: str
    points: list[HistoryPoint]
    range_periods: list[RangePeriod]


class HistoryPayload(BaseModel):
    biomarkers: list[BiomarkerHistory]
```

- [ ] **Step 4: Write the route**

Create `app/routes/history.py`:

```python
from fastapi import APIRouter, Depends

from ..auth import current_user
from ..models import (
    BiomarkerHistory,
    BiomarkerResult,
    Envelope,
    HistoryPayload,
    HistoryPoint,
    Member,
    RangePeriod,
    ResultsDocument,
)
from ..store import Store, get_store

router = APIRouter(prefix="/api")

HISTORY_LIMIT = 30


def biomarker_history(
    store: Store, user_id: str, limit: int = HISTORY_LIMIT
) -> tuple[list[BiomarkerHistory], int, bool]:
    """Every marker's history across the member's latest `limit` draws.

    Each draw document holds all the markers measured that day, so one marker's
    history means reading every draw. We read them once and split by marker.
    `keys()` finds the window without decoding anything; the range query then
    decodes at most `limit` documents however long the history gets.

    Returns (histories, draws read, whether older draws were left out).
    """
    partition = f"results:{user_id}"
    sort_keys = list(store.keys(partition))
    if not sort_keys:
        return [], 0, False

    window = sort_keys[-limit:]
    by_marker: dict[str, list[tuple[str, BiomarkerResult]]] = {}
    for _sk, draw in store.query(partition, ResultsDocument, start=window[0]):
        for result in draw.results:
            by_marker.setdefault(result.biomarker_id, []).append(
                (draw.tested_at, result)
            )

    histories = [
        marker_history(marker_id, entries)
        for marker_id, entries in sorted(by_marker.items())
    ]
    return histories, len(window), len(sort_keys) > limit


def marker_history(
    marker_id: str, entries: list[tuple[str, BiomarkerResult]]
) -> BiomarkerHistory:
    """Points oldest first, in the latest unit, with ranges as change periods."""
    unit = entries[-1][1].unit
    points: list[HistoryPoint] = []
    periods: list[RangePeriod] = []
    for tested_at, result in entries:
        # No conversion: a point in another unit would sit on the wrong scale.
        if result.unit != unit:
            continue
        points.append(
            HistoryPoint(tested_at=tested_at, value=result.value, status=result.status)
        )
        if not periods or periods[-1].ranges != result.ranges:
            periods.append(RangePeriod(from_tested_at=tested_at, ranges=result.ranges))
    return BiomarkerHistory(
        biomarker_id=marker_id, unit=unit, points=points, range_periods=periods
    )


@router.get("/biomarkers/history", response_model=Envelope[HistoryPayload])
def history(member: Member = Depends(current_user), store: Store = Depends(get_store)):
    """Biomarker history for the signed-in member. Not part of /api/home, so it
    does not count against the home budget; the client fetches it on demand."""
    histories, draws, truncated = biomarker_history(store, member.id)
    return Envelope(
        data=HistoryPayload(biomarkers=histories),
        meta={"draws": draws, "truncated": truncated},
    )
```

- [ ] **Step 5: Register the router**

In `app/main.py`, change `from .routes import auth, home, ingest` to `from .routes import auth, history, home, ingest`, and add after `app.include_router(home.router)`:

```python
app.include_router(history.router)
```

The route is `/api/biomarkers/history`. `home.router` has `/api/biomarkers` but no `/api/biomarkers/{param}` route, so there's no clash.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_biomarker_history.py -q`
Expected: 9 passed.

- [ ] **Step 7: Run the full suite and lint**

Run: `scripts/test && scripts/lint`
Expected: all pass. The existing `tests/test_home.py` passes unchanged. `test_wearables.py` still has its strict xfail and still xfails. If `scripts/fmt` would reformat, run `scripts/fmt` and re-run lint.

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/routes/history.py app/main.py tests/test_biomarker_history.py
git commit -m "Add GET /api/biomarkers/history: latest 30 draws, ranges as periods"
```

---

### Task 2: Reusable line chart with reference bands

**Files:**
- Create: `app/static/charts.js`
- Modify: `app/static/index.html`. Add the script tag between `perf.js` and `app.js`.
- Modify: `app/static/styles.css`. Add `.history-chart`.

**Interfaces:**
- Consumes: global `Chart` (Chart.js 4.5.1), CSS variables `--optimal`, `--good`, `--improve`, `--ink`.
- Produces: `window.lineChart(canvas, { points, unit, periods })` returning a `Chart`.
  - `points`: `[{ label: string, value: number | null, status?: "optimal" | "good" | "improve" }]`
  - `unit`: string
  - `periods` (optional): `[{ startIndex: number, ranges: { optimal: {min,max}, good: {min,max}, improve: {min,max} } }]`. Omit it to get no bands; Task 2 wearables will do this.

- [ ] **Step 1: Write `app/static/charts.js`**

```js
/* Line chart for member history: biomarkers now, wearables next.
 *
 *   lineChart(canvas, { points, unit, periods })
 *
 * points   [{ label, value, status? }]  value may be null: the line breaks there.
 * unit     shown on the y axis and in the tooltip.
 * periods  optional [{ startIndex, ranges }]: reference ranges that apply from
 *          points[startIndex] until the next period. Drawn as translucent bands.
 *
 * The x axis is a category axis of preformatted labels, so no date adapter.
 */

(function () {
  const BAND_LABEL = { optimal: "Optimal", good: "Good", improve: "Needs work" };
  const BAND_ALPHA = 0.18;

  function cssColor(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(`--${name}`)
      .trim();
  }

  function withAlpha(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  /* One set of band rectangles per period, across the x-slices of the points it
     covers. The slice first gets "improve", then each band clears and paints its
     own strip, so anything no band covers stays red, matching band_for() on the
     server, and translucent colours never stack. */
  function bandsPlugin(periods) {
    return {
      id: "referenceBands",
      beforeDatasetsDraw(chart) {
        if (!periods.length) return;
        const { ctx, chartArea: area, scales } = chart;
        const count = chart.data.labels.length;
        const fill = Object.fromEntries(
          ["optimal", "good", "improve"].map((b) => [b, withAlpha(cssColor(b), BAND_ALPHA)])
        );
        const edge = (i) =>
          i <= 0
            ? area.left
            : i >= count
              ? area.right
              : (scales.x.getPixelForValue(i - 1) + scales.x.getPixelForValue(i)) / 2;

        ctx.save();
        ctx.beginPath();
        ctx.rect(area.left, area.top, area.width, area.height);
        ctx.clip();
        periods.forEach((period, p) => {
          const left = edge(period.startIndex);
          const right = edge(periods[p + 1]?.startIndex ?? count);
          ctx.fillStyle = fill.improve;
          ctx.fillRect(left, area.top, right - left, area.height);
          for (const [band, { min, max }] of Object.entries(period.ranges)) {
            const top = scales.y.getPixelForValue(max);
            const bottom = scales.y.getPixelForValue(min);
            ctx.clearRect(left, top, right - left, bottom - top);
            ctx.fillStyle = fill[band];
            ctx.fillRect(left, top, right - left, bottom - top);
          }
        });
        ctx.restore();
      },
    };
  }

  /* Fit the data, widened to the nearest band edge on each side so the member
     can see which band a point is near. Not the full band span: that can be
     0–400 and would flatten the line. */
  function yBounds(values, periods) {
    const present = values.filter((v) => v != null);
    if (!present.length) return {};
    let lo = Math.min(...present);
    let hi = Math.max(...present);
    const edges = periods.flatMap((p) =>
      Object.values(p.ranges).flatMap((b) => [b.min, b.max])
    );
    const below = edges.filter((e) => e <= lo);
    const above = edges.filter((e) => e >= hi);
    if (below.length) lo = Math.max(...below);
    if (above.length) hi = Math.min(...above);
    const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.1 || 1;
    return { min: lo >= 0 ? Math.max(0, lo - pad) : lo - pad, max: hi + pad };
  }

  window.lineChart = function (canvas, { points, unit, periods = [] }) {
    const ink = cssColor("ink");
    const pointColor = (p) => (p.status ? cssColor(p.status) : ink);
    const values = points.map((p) => p.value);

    return new Chart(canvas, {
      type: "line",
      data: {
        labels: points.map((p) => p.label),
        datasets: [
          {
            label: unit,
            data: values,
            borderColor: ink,
            borderWidth: 1.5,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointBackgroundColor: points.map(pointColor),
            pointBorderColor: points.map(pointColor),
            spanGaps: false,
          },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { ...yBounds(values, periods), title: { display: true, text: unit } },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label(item) {
                const p = points[item.dataIndex];
                const band = BAND_LABEL[p.status];
                return `${p.value} ${unit}${band ? ` · ${band}` : ""}`;
              },
            },
          },
        },
      },
      plugins: [bandsPlugin(periods)],
    });
  };
})();
```

- [ ] **Step 2: Load it and give the chart a height**

In `app/static/index.html`, between `<script src="/static/perf.js"></script>` and `<script src="/static/app.js"></script>`:

```html
<script src="/static/charts.js"></script>
```

Append to `app/static/styles.css`:

```css
/* ---- history ---- */

.history-chart {
  position: relative;
  height: 180px;
}
```

- [ ] **Step 3: Smoke-check the chart in the browser console**

Run `scripts/dev` (in the background) and open http://localhost:8000. Sign in as james.chen@example.com, then paste into the devtools console:

```js
const c = document.createElement("div"); c.className = "history-chart";
const cv = document.createElement("canvas"); c.append(cv); document.querySelector("main").prepend(c);
lineChart(cv, { unit: "mg/dL",
  points: [{label:"Jan 25",value:8,status:"improve"},{label:"Jun 25",value:15,status:"good"},{label:"Jan 26",value:12,status:"optimal"}],
  periods: [{startIndex:0,ranges:{optimal:{min:10,max:18},good:{min:18,max:22},improve:{min:22,max:60}}},
            {startIndex:2,ranges:{optimal:{min:10,max:16},good:{min:16,max:22},improve:{min:22,max:60}}}] });
```

Expected:
- Green band 10–18 for the first two slices, stepping to 10–16 in the third.
- Amber above green.
- Red below 10, behind the 8 point, which is visible (Review Focus 2).
- The tooltip on the 8 point reads "8 mg/dL · Needs work".
- No console errors.

Reload the page to remove the test chart.

- [ ] **Step 4: Commit**

```bash
git add app/static/charts.js app/static/index.html app/static/styles.css
git commit -m "Add reusable lineChart with translucent reference bands"
```

---

### Task 3: History toggle on marker cards

**Files:**
- Modify: `app/static/app.js`. Add the history cache, `renderHistory`, the toggle in `renderMarker`, and cache resets.
- Modify: `app/static/styles.css`. Add toggle and message styles.

**Interfaces:**
- Consumes:
  - `GET /api/biomarkers/history` (Task 1)
  - `window.lineChart(canvas, { points, unit, periods })` (Task 2)
- Produces: nothing for later tasks.

- [ ] **Step 1: Add the cache and loader**

In `app/static/app.js`, after `formatDate`:

```js
function formatShortDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/* History for every marker arrives in one response (each draw holds all its
   markers), fetched on the first expand and shared by every card after that.
   A failed request is dropped so the next expand tries again. */
let historyRequest = null;

function loadHistory() {
  historyRequest ??= api("/api/biomarkers/history")
    .then(({ data, meta }) => ({
      byId: new Map(data.biomarkers.map((h) => [h.biomarker_id, h])),
      truncated: meta.truncated,
    }))
    .catch((err) => {
      historyRequest = null;
      throw err;
    });
  return historyRequest;
}

function note(text) {
  const p = document.createElement("p");
  p.className = "history-note";
  p.textContent = text;
  return p;
}

async function renderHistory(panel, result) {
  panel.dataset.rendered = "loading";
  panel.replaceChildren(note("Loading history…"));

  let history;
  try {
    history = await loadHistory();
  } catch {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "quiet";
    retry.textContent = "Try again";
    retry.addEventListener("click", () => renderHistory(panel, result));
    panel.replaceChildren(note("Couldn't load history."), retry);
    delete panel.dataset.rendered;
    return;
  }

  const marker = history.byId.get(result.biomarker_id);
  const points = marker?.points ?? [];
  if (points.length < 2) {
    panel.replaceChildren(
      note("Only one result so far. Your trend appears after your next test.")
    );
    panel.dataset.rendered = "done";
    return;
  }

  const wrap = document.createElement("div");
  wrap.className = "history-chart";
  const canvas = document.createElement("canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", `${result.name} history, ${points.length} results`);
  wrap.append(canvas);
  panel.replaceChildren(wrap);
  if (history.truncated) panel.append(note("Showing your last 30 tests."));

  lineChart(canvas, {
    unit: marker.unit,
    points: points.map((p) => ({
      label: formatShortDate(p.tested_at),
      value: p.value,
      status: p.status,
    })),
    periods: marker.range_periods.map((r) => ({
      startIndex: points.findIndex((p) => p.tested_at === r.from_tested_at),
      ranges: r.ranges,
    })),
  });
  panel.dataset.rendered = "done";
}
```

- [ ] **Step 2: Add the toggle to `renderMarker`**

In the `node.innerHTML` template in `renderMarker`, after the `<div class="scale-labels">…</div>` line, add:

```html
    <button type="button" class="quiet history-toggle" aria-expanded="false">History</button>
    <div class="history" hidden></div>
```

Then, just before `return node;` in `renderMarker`:

```js
  const toggle = node.querySelector(".history-toggle");
  const panel = node.querySelector(".history");
  panel.id = `history-${result.biomarker_id}`;
  toggle.setAttribute("aria-controls", panel.id);
  toggle.addEventListener("click", () => {
    const open = panel.hidden;
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    // Unhide first so Chart.js measures a visible container.
    if (open && !panel.dataset.rendered) renderHistory(panel, result);
  });
```

- [ ] **Step 3: Reset the cache when the member changes**

In `showDashboard`, as its first line:

```js
  historyRequest = null;
```

In the `signout-button` click handler, after `await api("/api/auth/logout", …)`:

```js
  historyRequest = null;
```

- [ ] **Step 4: Style the toggle and notes**

Append to `app/static/styles.css` under `/* ---- history ---- */`:

```css
.history-toggle {
  margin-top: 0.6rem;
  padding: 0.2rem 0.6rem;
  font-size: 0.8125rem;
}

.history { margin-top: 0.75rem; }

.history-note {
  margin: 0.5rem 0;
  color: var(--muted);
  font-size: 0.8125rem;
}
```

- [ ] **Step 5: Check that `/api/home` is still the only request on load**

With `scripts/dev` running, reload http://localhost:8000 signed in as james.chen@example.com. In the devtools Network tab (filter `/api/`), confirm that only `/api/me` and `/api/home` fire. No history request happens until a card is opened.

- [ ] **Step 6: Manual walkthrough (Review Focus 1, 3 and 5)**

As james.chen@example.com:
1. Open LDL cholesterol. Expect a chart with 5 points and green/amber/red bands that step where its ranges changed. The tooltip shows date, value with unit, and band.
2. Click History on a marker measured only in full draws, then a second card quickly. Network shows exactly **one** `/api/biomarkers/history` request.
3. Close and reopen a card. The chart is still there and there's no new request.
4. Sign out, then sign in as sofia.marek@example.com (2 draws). Open a card. A new history request fires, and the chart shows Sofia's 2 points, not James's.
5. Stop `scripts/dev` and open an unopened card. "Couldn't load history." and "Try again" appear. Restart `scripts/dev`, click "Try again", and the chart renders.

Also as sofia.marek@example.com: if any marker has a single point, it shows the "Only one result so far…" copy.

- [ ] **Step 7: Commit**

```bash
git add app/static/app.js app/static/styles.css
git commit -m "Add History toggle on marker cards with lazy, cached history fetch"
```

---

### Task 4: Verify, benchmark, record

**Files:**
- Modify: `NOTES.md`

**Interfaces:**
- Consumes: everything above.
- Produces: verified branch, NOTES.md with measured numbers, pushed to `submission`.

- [ ] **Step 1: Full suite and lint**

Run: `scripts/test && scripts/lint`
Expected: all pass. `test_wearables.py`'s strict xfail still xfails.

- [ ] **Step 2: Benchmark `/api/home` for all four members**

With `scripts/dev` running:

```bash
for e in james.chen priya.raman tom.okafor sofia.marek; do scripts/bench.py --email $e@example.com; done
```

Expected: every run exits 0 with p95 ≤ 35 ms. Record the four p95 values.

- [ ] **Step 3: Measure the history endpoint (informational, no budget)**

```bash
scripts/bench.py --path /api/biomarkers/history --email tom.okafor@example.com
```

Tom has the most draws (6). Record p50/p95. A non-zero exit here only means it's above the home budget, which doesn't apply to this endpoint.

- [ ] **Step 4: Update NOTES.md**

Under `## Task 1: Biomarker history`, add a `### Verification` section with the four `/api/home` p95 numbers, the history endpoint p50/p95, and the test count. Leave `## Task 2` as "Not started" unless work has begun.

- [ ] **Step 5: Revert lockfile drift, commit, push**

```bash
git checkout -- uv.lock
git add NOTES.md
git commit -m "NOTES: Task 1 verification numbers"
git push submission main
```
