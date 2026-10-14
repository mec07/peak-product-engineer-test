# Biomarker Trend Arrows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each biomarker card's status pill shows ↑ / ↓ / – for the band change between the marker's last two results. A tooltip and screen-reader text say the same thing in words.

**Architecture:**
- `latest_results` in `app/routes/home.py` reads the member's latest ≤30 draws with one `query`, which replaces today's `get`.
- It indexes each marker's previous same-unit result and adds `trend` plus `previous {status, tested_at}` to every `BiomarkerView`.
- The client maps `trend` to a coloured symbol, a `title` tooltip and `.sr-only` text inside the existing pill.

**Tech Stack:**
- FastAPI and Pydantic v2
- The project `Store` (public API only)
- Plain JS and CSS
- pytest (`scripts/test`) and ruff + ty (`scripts/lint`)
- Playwright 1.58 for the headless check, via `uv run --with`

**Spec:** `docs/superpowers/specs/2026-10-14-trend-arrows-design.md`

## Global Constraints

- Branch: `task-3-trend-arrows`. Every commit goes there. **Never push or merge to `main`.** It's one PR, and the user merges it.
- `/api/home` stays at or under 35 ms p95 (`scripts/bench.py`) for all four members.
- Use only the store's public API: `get`, `query`, `keys`, `scan`, `put`, `put_many`, `delete`, `close`. No `scan` on the home path.
- Don't edit `tests/test_store_api.py` or `app/auth.py`.
- After any `uv` command, run `git checkout -- uv.lock`. `uv` rewrites it locally, and that change must never be committed.
- Rank: `improve` 0 < `good` 1 < `optimal` 2. `trend` is `"up"`, `"down"`, `"same"` or `null`.
- `trend` and `previous` are both `null` or both set.
- "Previous" is the marker's most recent earlier result **in the same unit** within the latest `HISTORY_LIMIT` (30) draws.
- Colour tokens are exactly `--trend-up: #169443`, `--trend-down: #e5484d` and `--trend-same: #c2690a`.
- Symbols are exactly `↑`, `↓` and `–` (en dash).
- Copy is exactly "Improved from {label}", "Worse than {label}" and "No change from {label}", each followed by ` · {short date}`. Labels come from `BAND_LABEL` (Optimal / Good / Needs work).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Partial draws:** a marker missing from the draws just before the latest still finds its own earlier result. Covered by Task 1 `test_partial_draws_use_the_markers_own_previous_result`.
2. **The arrow must agree with the chart the member can open.** On the real seeded data, `previous` equals the History chart's second-to-last point for every card. Covered by Task 1 `test_trend_agrees_with_the_history_charts_last_two_dots`.
3. **An unknown or legacy status string** (not one of the three bands) gives no arrow, not a 500 and not a wrong arrow. Covered by Task 1's `trend()` parametrised cases.
4. **A member with no draws** still gets `results: []` with no error, now that `get` has become `query`. Covered by the existing `test_home_without_draws`, which Task 1 step 4 runs.
5. **Screen readers** must hear the trend as part of the card button's name, with a space between "Good" and the sentence. Covered by Task 2 step 4, which reads the button's text through Playwright.

---

### Task 1: Server: `trend` and `previous` on every result

**Files:**
- Modify: `app/models.py` (add `Trend`, `PreviousResult`; extend `BiomarkerView`, around lines 107–117)
- Modify: `app/routes/home.py` (imports, new `RANK` / `trend` / `previous_results`, rewrite `latest_results` lines 35–79)
- Create: `tests/test_trend.py`

**Interfaces:**
- Consumes: `HISTORY_LIMIT` from `app/routes/history.py` (an int, 30).
- Produces:
  - In `app.models`:
    - `Trend = Literal["up", "down", "same"]`
    - `PreviousResult(status: str, tested_at: str)`
    - `BiomarkerView.trend: Trend | None = None`
    - `BiomarkerView.previous: PreviousResult | None = None`
  - In `app.routes.home`:
    - `RANK: dict[str, int]`
    - `trend(previous: str, latest: str) -> Trend | None`
    - `previous_results(earlier: list[ResultsDocument]) -> dict[tuple[str, str], PreviousResult]`
  - JSON: each `/api/home` and `/api/biomarkers` result gains `"trend"` and `"previous"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_trend.py`. The tests write draws straight to the store, as `tests/test_biomarker_history.py` does, so each test controls the stored status exactly. The ingest route would derive the status from the value instead.

```python
"""Trend arrows: each /api/home result carries `trend` and `previous`.

The seeded dev.db already holds draws for every member. The `add_draw` fixture
clears user-1's and user-2's partitions first, and each test writes exactly the
draws it needs with the statuses it wants. Statuses are stored, not derived, so
the arrow compares what the member was told at each draw.
"""

import pytest

from app.models import BiomarkerResult, Ranges, ResultsDocument
from app.routes.home import trend

RANGES = Ranges(
    optimal={"min": 10, "max": 20},
    good={"min": 5, "max": 25},
    improve={"min": 0, "max": 30},
)


def result(
    marker_id: str, status: str, *, value: float = 15, unit: str = "mg/dL"
) -> BiomarkerResult:
    return BiomarkerResult(
        biomarker_id=marker_id, value=value, unit=unit, status=status, ranges=RANGES
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


def home_by_id(client) -> dict[str, dict]:
    response = client.get("/api/home")
    assert response.status_code == 200
    return {r["biomarker_id"]: r for r in response.json()["data"]["results"]}


@pytest.mark.parametrize(
    ("previous", "latest", "expected"),
    [
        ("improve", "good", "up"),
        ("good", "optimal", "up"),
        ("improve", "optimal", "up"),
        ("optimal", "good", "down"),
        ("good", "improve", "down"),
        ("optimal", "improve", "down"),
        ("optimal", "optimal", "same"),
        ("good", "good", "same"),
        ("improve", "improve", "same"),
        ("borderline", "good", None),
        ("good", "borderline", None),
    ],
)
def test_trend_ranks_bands(previous, latest, expected):
    assert trend(previous, latest) == expected


def test_better_band_is_up_with_the_previous_result(signed_in, add_draw):
    add_draw("user-1", "2026-03-12T09:00:00", result("a", "improve"))
    add_draw("user-1", "2026-09-01T09:00:00", result("a", "good"))

    a = home_by_id(signed_in)["a"]
    assert a["trend"] == "up"
    assert a["previous"] == {"status": "improve", "tested_at": "2026-03-12T09:00:00"}

    listed = signed_in.get("/api/biomarkers").json()["data"]["results"]
    assert [(r["trend"], r["previous"]) for r in listed] == [
        ("up", {"status": "improve", "tested_at": "2026-03-12T09:00:00"})
    ]


def test_worse_band_is_down(signed_in, add_draw):
    add_draw("user-1", "2026-03-12T09:00:00", result("a", "optimal"))
    add_draw("user-1", "2026-09-01T09:00:00", result("a", "good"))

    assert home_by_id(signed_in)["a"]["trend"] == "down"


def test_same_band_with_a_new_value_is_same(signed_in, add_draw):
    add_draw("user-1", "2026-03-12T09:00:00", result("a", "good", value=6))
    add_draw("user-1", "2026-09-01T09:00:00", result("a", "good", value=24))

    assert home_by_id(signed_in)["a"]["trend"] == "same"


def test_first_result_has_no_trend(signed_in, add_draw):
    add_draw("user-1", "2026-09-01T09:00:00", result("a", "good"))

    a = home_by_id(signed_in)["a"]
    assert a["trend"] is None
    assert a["previous"] is None


def test_partial_draws_use_the_markers_own_previous_result(signed_in, add_draw):
    add_draw(
        "user-1", "2025-01-01T09:00:00", result("a", "improve"), result("b", "good")
    )
    add_draw("user-1", "2025-04-01T09:00:00", result("b", "good"))
    add_draw("user-1", "2025-07-01T09:00:00", result("b", "optimal"))
    add_draw(
        "user-1", "2025-10-01T09:00:00", result("a", "optimal"), result("b", "optimal")
    )

    home = home_by_id(signed_in)
    assert home["a"]["trend"] == "up"
    assert home["a"]["previous"]["tested_at"] == "2025-01-01T09:00:00"
    assert home["b"]["trend"] == "same"
    assert home["b"]["previous"]["tested_at"] == "2025-07-01T09:00:00"


def test_previous_result_in_another_unit_is_skipped(signed_in, add_draw):
    add_draw(
        "user-1",
        "2025-01-01T09:00:00",
        result("a", "optimal"),
        result("b", "good", unit="mmol/L"),
    )
    add_draw("user-1", "2025-06-01T09:00:00", result("a", "improve", unit="mmol/L"))
    add_draw("user-1", "2026-01-01T09:00:00", result("a", "good"), result("b", "good"))

    home = home_by_id(signed_in)
    assert home["a"]["trend"] == "down"
    assert home["a"]["previous"]["tested_at"] == "2025-01-01T09:00:00"
    assert home["b"]["trend"] is None
    assert home["b"]["previous"] is None


@pytest.mark.parametrize(("fillers", "expected"), [(28, "up"), (29, None)])
def test_previous_result_must_be_in_the_latest_30_draws(
    signed_in, add_draw, fillers, expected
):
    # 1 + fillers + 1 draws: 30 keeps the first draw in the window, 31 drops it.
    add_draw("user-1", "2024-01-01T09:00:00", result("a", "improve"))
    for day in range(2, 2 + fillers):
        add_draw("user-1", f"2024-01-{day:02d}T09:00:00", result("b", "good"))
    add_draw("user-1", "2024-02-01T09:00:00", result("a", "good"))

    assert home_by_id(signed_in)["a"]["trend"] == expected


def test_other_members_draws_never_count(signed_in, add_draw):
    add_draw("user-2", "2025-01-01T09:00:00", result("a", "improve"))
    add_draw("user-1", "2026-01-01T09:00:00", result("a", "good"))

    assert home_by_id(signed_in)["a"]["trend"] is None


def test_trend_agrees_with_the_history_charts_last_two_dots(signed_in):
    # Real seeded data for user-1: partial draws, so previous results sit 1–2
    # draws back. No add_draw fixture here, so nothing is cleared.
    home = home_by_id(signed_in)
    response = signed_in.get("/api/biomarkers/history")
    history = {h["biomarker_id"]: h for h in response.json()["data"]["biomarkers"]}

    assert home
    for marker_id, latest in home.items():
        points = history[marker_id]["points"]
        if len(points) < 2:
            assert latest["trend"] is None
            continue
        before, last = points[-2], points[-1]
        assert latest["previous"] == {
            "status": before["status"],
            "tested_at": before["tested_at"],
        }
        assert latest["trend"] == trend(before["status"], last["status"])
```

- [ ] **Step 2: Run the tests to check they fail**

Run: `uv run pytest tests/test_trend.py -q; git checkout -- uv.lock`

Expected: collection error `ImportError: cannot import name 'trend' from 'app.routes.home'`.

- [ ] **Step 3: Add the models**

In `app/models.py`:

1. Change the typing import on line 2 to `from typing import Literal, TypeVar`.
2. Add these two definitions directly above `class BiomarkerView`:

```python
Trend = Literal["up", "down", "same"]


class PreviousResult(BaseModel):
    """The marker's previous result: the History chart's second-to-last dot."""

    status: str
    tested_at: str
```

3. Add two fields at the end of `BiomarkerView`, after `ranges: Ranges`:

```python
    trend: Trend | None = None
    previous: PreviousResult | None = None
```

- [ ] **Step 4: Compute the trend in `latest_results`**

In `app/routes/home.py`:

1. Add `PreviousResult` and `Trend` to the `from ..models import (...)` list, keeping it alphabetical.
2. Add `from .history import HISTORY_LIMIT` below the `from ..wearables import ...` line.
3. Replace the whole `latest_results` function (lines 35–79) with the code below. `RANK`, `trend` and `previous_results` go directly above it.

```python
RANK = {"improve": 0, "good": 1, "optimal": 2}


def trend(previous: str, latest: str) -> Trend | None:
    """Band movement between two statuses; None if either is unknown."""
    if previous not in RANK or latest not in RANK:
        return None
    change = RANK[latest] - RANK[previous]
    return "up" if change > 0 else "down" if change < 0 else "same"


def previous_results(
    earlier: list[ResultsDocument],
) -> dict[tuple[str, str], PreviousResult]:
    """Each (marker, unit)'s most recent result among the earlier draws.

    Keyed by unit as well as marker, the same guard the History chart uses, so a
    marker's previous result is always the chart's second-to-last dot. Draws are
    partial, so that dot can be several draws back.
    """
    found: dict[tuple[str, str], PreviousResult] = {}
    for draw in reversed(earlier):
        for result in draw.results:
            key = (result.biomarker_id, result.unit)
            if key not in found:
                found[key] = PreviousResult(
                    status=result.status, tested_at=draw.tested_at
                )
    return found


def latest_results(
    store: Store, user_id: str
) -> tuple[list[BiomarkerView], str | None]:
    """Every biomarker with this member's most recent value and its trend.

    One partition per member, one document per draw, sorted by ISO timestamp. So
    the last sort key in the partition is the latest draw. The query reads the
    same window the History chart plots (the latest HISTORY_LIMIT draws): the
    last draw gives the values, the earlier ones only each marker's previous
    result. That keeps the store calls the same as reading the latest draw alone.
    """
    partition = f"results:{user_id}"
    sort_keys = list(store.keys(partition))
    if not sort_keys:
        return [], None

    window = sort_keys[-HISTORY_LIMIT:]
    draws = [
        draw for _sk, draw in store.query(partition, ResultsDocument, start=window[0])
    ]
    if not draws:
        return [], None
    draw = draws[-1]
    earlier = previous_results(draws[:-1])

    defs = definitions(store)

    views = []
    for result in draw.results:
        definition = defs.get(result.biomarker_id)
        before = earlier.get((result.biomarker_id, result.unit))
        change = trend(before.status, result.status) if before else None
        views.append(
            BiomarkerView(
                biomarker_id=result.biomarker_id,
                name=definition.name if definition else result.biomarker_id,
                category=definition.category if definition else "other",
                description=definition.description if definition else "",
                direction=definition.direction if definition else "in_range",
                value=result.value,
                unit=result.unit,
                tested_at=draw.tested_at,
                status=result.status,
                ranges=result.ranges,
                trend=change,
                previous=before if change else None,
            )
        )

    views.sort(key=lambda v: (v.category, v.name))
    return views, draw.tested_at
```

- [ ] **Step 5: Run the new tests and the full suite**

Run: `uv run pytest tests/test_trend.py -q && scripts/test && scripts/lint; git checkout -- uv.lock`

Expected:
- `tests/test_trend.py`: 21 passed (11 `trend()` cases plus 10 HTTP tests).
- Full suite: 97 passed. That's 76 before plus 21.
- Lint is clean.

If `ruff format --check` fails, run `uv run ruff format app tests`, then `git checkout -- uv.lock`, and re-run lint. Don't run `ruff format` on `docs/`, because it rewrites the plan's code blocks.

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/routes/home.py tests/test_trend.py
git commit -m "Trend and previous result on every home result

Compares each marker's latest band with its own previous result (same
unit, latest 30 draws). One query over the window replaces the get of
the latest draw, so /api/home makes the same number of store calls.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Client: symbol, tooltip and screen-reader text in the pill

**Files:**
- Modify: `app/static/app.js` (new `TREND` and `trendParts` above `renderMarker`; `renderMarker` lines 168–169)
- Modify: `app/static/styles.css` (`:root` lines 1–10; the pill block around lines 213–229)

**Interfaces:**
- Consumes: `result.trend` (`"up" | "down" | "same" | null`) and `result.previous` (`{status, tested_at} | null`) from Task 1. Also the existing `BAND_LABEL` and `formatShortDate`.
- Produces: the `.trend`, `.trend-up`, `.trend-down`, `.trend-same` and `.sr-only` classes. The pill gets a `title` when there's a trend.

- [ ] **Step 1: Add the trend helper**

In `app/static/app.js`, insert directly above `function renderMarker(result) {`:

```js
const TREND = {
  up: { symbol: "↑", verb: "Improved from" },
  down: { symbol: "↓", verb: "Worse than" },
  same: { symbol: "–", verb: "No change from" },
};

/* The arrow compares the marker's band with its previous result, the last two
   dots on its History chart. Colour and symbol alone aren't enough, so the same
   sentence is the pill's hover tooltip and is read out with the card's button.
   Returns the sentence and the nodes to append, or null for a first result. */
function trendParts(result) {
  const t = TREND[result.trend];
  if (!t || !result.previous) return null;

  const was = BAND_LABEL[result.previous.status] ?? result.previous.status;
  const sentence = `${t.verb} ${was} · ${formatShortDate(result.previous.tested_at)}`;

  const symbol = document.createElement("span");
  symbol.className = `trend trend-${result.trend}`;
  symbol.setAttribute("aria-hidden", "true");
  symbol.textContent = t.symbol;

  const spoken = document.createElement("span");
  spoken.className = "sr-only";
  // Leading space, so it isn't read as one word with the label.
  spoken.textContent = ` ${sentence}`;

  return { sentence, nodes: [symbol, spoken] };
}
```

- [ ] **Step 2: Render it in the pill**

In `renderMarker`, replace:

```js
  node.querySelector(".marker-status").textContent =
    BAND_LABEL[result.status] ?? result.status;
```

with:

```js
  const status = node.querySelector(".marker-status");
  status.textContent = BAND_LABEL[result.status] ?? result.status;
  const trend = trendParts(result);
  if (trend) {
    status.append(...trend.nodes);
    status.title = trend.sentence;
  }
```

The `title` sits on the whole pill rather than the symbol, so the hover target is the full pill instead of one character. This is a deliberate refinement of the spec, which put it on the symbol. Task 3 updates the spec to match.

- [ ] **Step 3: Add the styles**

In `app/static/styles.css`, add these three lines inside the existing `:root { ... }` block, after `--track: #e8ebe9;`:

```css
  --trend-up: #169443;
  --trend-down: #e5484d;
  --trend-same: #c2690a;
```

Directly after the line `.status-improve { color: var(--improve); }`, add:

```css
/* Trend symbols are brighter than the pill text so they stand out, and each
   keeps at least 3:1 against all three pill tints (WCAG 1.4.11). */
.trend {
  margin-left: 0.3rem;
  font-weight: 700;
}

.trend-up { color: var(--trend-up); }
.trend-down { color: var(--trend-down); }
.trend-same { color: var(--trend-same); }

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}
```

- [ ] **Step 4: Check it headless against the real data**

1. Start the server in the background: `scripts/dev`. It serves `dev.db`, which already holds the fixtures and the ingested wearables.
2. Write `$SCRATCHPAD/trend_check.py` with the script below.
3. Run it: `uv run --with playwright==1.58.0 python $SCRATCHPAD/trend_check.py`, then `git checkout -- uv.lock`. If Chromium is missing, run `uv run --with playwright==1.58.0 playwright install chromium` first.

```python
"""Headless check of the trend pills for all four members."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT = Path(sys.argv[0]).parent
SYMBOL = {"up": "↑", "down": "↓", "same": "–"}
VERB = {"up": "Improved from", "down": "Worse than", "same": "No change from"}
LABEL = {"optimal": "Optimal", "good": "Good", "improve": "Needs work"}

with sync_playwright() as p:
    browser = p.chromium.launch()
    for email in ("james.chen", "priya.raman", "tom.okafor", "sofia.marek"):
        page = browser.new_page(viewport={"width": 420, "height": 2400})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE)
        page.fill("#email", f"{email}@example.com")
        page.click("#signin-button")
        page.wait_for_selector(".marker-status")

        results = page.evaluate(
            "fetch('/api/home').then(r => r.json()).then(b => b.data.results)"
        )
        pills = page.eval_on_selector_all(
            "#results .marker",
            """cards => cards.map(c => {
                const s = c.querySelector('.marker-status');
                const t = s.querySelector('.trend');
                return {
                  symbol: t ? t.textContent : null,
                  colour: t ? getComputedStyle(t).color : null,
                  title: s.title,
                  spoken: s.querySelector('.sr-only')?.textContent ?? null,
                  button: c.querySelector('.history-toggle').innerText,
                  name: c.querySelector('.marker-name').textContent,
                };
            })""",
        )
        assert len(pills) == len(results), (len(pills), len(results))
        counts = {"up": 0, "down": 0, "same": 0, None: 0}
        for r, pill in zip(results, pills, strict=True):
            assert pill["name"] == r["name"], (pill["name"], r["name"])
            counts[r["trend"]] += 1
            if r["trend"] is None:
                assert pill["symbol"] is None and pill["title"] == "", pill
                continue
            assert pill["symbol"] == SYMBOL[r["trend"]], (r["name"], pill)
            assert pill["title"].startswith(
                f"{VERB[r['trend']]} {LABEL[r['previous']['status']]} · "
            ), pill
            assert pill["spoken"] == f" {pill['title']}", pill
            # The sentence is part of the card button's text (its accessible name).
            assert pill["title"] in pill["button"], pill
        assert not errors, errors
        page.screenshot(path=str(OUT / f"trend-{email}.png"), full_page=False)
        print(email, counts, "sample:", pills[0]["title"] or "(no trend)")
        page.close()
    browser.close()
```

Expected:
- Four lines with these counts, which match the spec's data table:

  | Member | up | down | same | None |
  |---|---|---|---|---|
  | james.chen | 9 | 9 | 12 | 0 |
  | priya.raman | 11 | 6 | 13 | 0 |
  | tom.okafor | 8 | 9 | 13 | 0 |
  | sofia.marek | 13 | 8 | 9 | 0 |

- No assertion errors and no page errors.

Then look at `$SCRATCHPAD/trend-james.chen.png` with the Read tool and check:
- The three symbol colours are visible on all three pill tints.
- Pills with and without a symbol don't break the row layout. A long "Needs work –" pill may be wider than the others, which is fine. If it wraps onto two lines, add `white-space: nowrap;` to `.marker-status` and re-run.

- [ ] **Step 5: Commit**

```bash
git add app/static/app.js app/static/styles.css
git commit -m "Trend symbol, tooltip and screen-reader text in the status pill

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Verify, record, raise the PR

**Files:**
- Modify: `NOTES.md`
- Modify: `docs/superpowers/specs/2026-10-14-trend-arrows-design.md` (two lines)

**Interfaces:**
- Consumes: everything above.
- Produces: the Task 3 PR.

- [ ] **Step 1: Suite and lint**

Run: `scripts/test && scripts/lint; git checkout -- uv.lock`

Expected: 97 passed, and lint is clean.

- [ ] **Step 2: Benchmark `/api/home`**

With `scripts/dev` running:

```bash
for e in james.chen priya.raman tom.okafor sofia.marek; do scripts/bench.py --email $e@example.com; done
```

Expected: every run reports `PASS` with p95 ≤ 35 ms. Before this change, the values were 6.7–10.2 ms. If one run fails while the others pass, re-run that member 3 times and record all the values, because bench is noisy on a loaded laptop.

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
- `store:query pk=results:user-3, …` (this replaces the old `store:get`)
- `store:query pk=biomarker, …`
- `store:keys pk=wearables:user-3, …`
- `store:query pk=wearables:user-3, …`

There must be no `scan`.

- [ ] **Step 4: Bring the spec in line with what was built**

In `docs/superpowers/specs/2026-10-14-trend-arrows-design.md`, make three edits:

1. In `## Tests`, replace:

   > It follows the `tests/test_home.py` fixtures and posts draws through the existing ingest route.

   with:

   > It writes draws straight to the store, as `tests/test_biomarker_history.py` does, so each test controls the stored status.

   Also add this bullet to the numbered list: "9. **Agrees with the chart:** on the seeded data, `previous` is the History chart's second-to-last point for every card."

2. In the pill section, replace:

   > **Hover:** the native `title` tooltip on the symbol.

   with:

   > **Hover:** the native `title` tooltip on the whole pill, which is a larger target than the symbol.

3. In the HTML example, move the `title="…"` attribute from the inner `<span class="trend …">` to the outer `<span class="marker-status …">`.

- [ ] **Step 5: Update NOTES.md**

Insert this section **above** `## How I used AI tools`:

```markdown
## Task 3: Biomarker trend arrows

Spec: [docs/superpowers/specs/2026-10-14-trend-arrows-design.md](docs/superpowers/specs/2026-10-14-trend-arrows-design.md)

### Decisions

- **The arrow means "moved band", not "the value went up".** ↑ better band,
  ↓ worse band, – same band. Rank: Needs work < Good < Optimal, the same for
  every marker, since status already encodes the marker's direction.
- **Compared with the marker's own previous result**, the last two dots on its
  History chart. Draws are partial, so that result is often 2–5 draws back.
  Comparing with the previous draw only would leave about 70% of cards with no
  arrow.
- **A first result shows no symbol.** "–" would claim "no change", which we don't
  know.
- **Computed in `/api/home`.** One `query` over the latest ≤30 draws replaces the
  `get` of the latest draw, so the number of store calls is unchanged. Each result
  gains `trend` and `previous {status, tested_at}`.
  - Rejected: storing the previous status at ingest (needs a backfill, and an
    out-of-order draw breaks it) and computing it client-side from history (no
    arrows until History is opened).
- **Bright symbol colours** (green `#169443`, red `#e5484d`, amber `#c2690a`),
  each at least 3:1 against every pill tint.
- **Not colour-only:** the pill's tooltip and the card button's screen-reader text
  say "Improved from Needs work · 12 Mar 2026".

### Assumptions

- The arrow compares **stored** statuses, as the member was told at each draw. A
  range change between draws can move the arrow even if the value didn't.
- A previous result in another unit, or more than 30 draws back, isn't used. This
  matches the History chart.

### Verification

- [fill in: test count, the 4 bench p95 values, the store-call lines from step 3, the per-member ↑/↓/– counts from Task 2 step 4]
```

Replace the `[fill in: …]` line with the measured numbers from steps 1–3 and Task 2 step 4. It's an instruction to you, not text to leave in. Also append one bullet to `## How I used AI tools`: "Task 3 followed the same spec → plan → execution workflow. The data profile (how far back each marker's previous result sits) drove the 'own previous result' decision."

- [ ] **Step 6: Commit, push the branch, raise the PR**

Write `$SCRATCHPAD/pr-task-3.md` first. It needs:
- A `## Summary`: what the arrows mean, own previous result, computed in `/api/home` with the same store calls, colours, tooltip and screen-reader text.
- A `## Test plan` checklist with the real numbers from steps 1–3 and Task 2 step 4.
- This line at the end:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Then:

```bash
git add NOTES.md docs/superpowers/specs/2026-10-14-trend-arrows-design.md
git commit -m "NOTES: Task 3 trend arrows decisions and verification

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u submission task-3-trend-arrows
gh pr create -R mec07/peak-product-engineer-test --base main --head task-3-trend-arrows \
  --title "Task 3: biomarker trend arrows" \
  --body-file "$SCRATCHPAD/pr-task-3.md"
```

**Do not merge.** Report the PR URL. Any later amendments are new commits pushed to `task-3-trend-arrows`, so they land on this same PR.
