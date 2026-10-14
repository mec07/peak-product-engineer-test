# Task 3: Biomarker trend arrows — design

Status: draft for review. Decisions taken in chat:
- Compare against the marker's own previous result.
- A single result shows no symbol.
- Bare coloured symbols, plus a tooltip and screen-reader text.
- Compute the trend in `/api/home`.

Tasks 1 and 2 (merged) must keep working. Delivered as one PR from branch
`task-3-trend-arrows`.

## Goal

Each biomarker card's status pill shows whether the marker's **band** moved
between its last two results, the last two dots on its History chart:

| Symbol | Meaning | Example |
|---|---|---|
| ↑ | better band | Needs work → Good, Good → Optimal |
| ↓ | worse band | Optimal → Good |
| – | same band | Good → Good |
| *(none)* | no earlier result to compare | first test |

The arrow means "moved band", not "the value went up". Rank is the same for
every marker: `improve` (Needs work) 0 < `good` 1 < `optimal` 2. The marker's
`direction` doesn't matter, because status already encodes it.

Success:
- Every card shows its trend on page load, without opening History.
- The meaning isn't carried by colour alone.
- `/api/home` stays ≤ 35 ms p95 in `scripts/bench.py`.

## Data facts that drive the design

From `dev.db` (bootstrap fixtures):

| Member | Draws | Latest markers | Previous result found N draws back | ↑ / ↓ / – |
|---|---|---|---|---|
| user-1 | 5 | 30 | 1: 8, 2: 22 | 9 / 9 / 12 |
| user-2 | 3 | 30 | 1: 6, 2: 24 | 11 / 6 / 13 |
| user-3 | 6 | 30 | 1: 5, 2: 10, 3: 4, 5: 11 | 8 / 9 / 13 |
| user-4 | 2 | 30 | 1: 30 | 13 / 8 / 9 |

- Draws are partial. A marker's previous result is usually **not** in the draw
  immediately before, and can be 5 draws back. Looking at one previous draw would
  leave about 70% of cards with no arrow.
- `/api/home` currently reads only the latest draw. History is fetched lazily on
  first expand, so the client can't work out the trend on load.

## API

### Models (`app/models.py`)

```python
class PreviousResult(BaseModel):
    status: str  # optimal | good | improve, as stored on that draw
    tested_at: str  # ISO timestamp of that draw


class BiomarkerView(BaseModel):
    # ...existing fields unchanged...
    trend: Literal["up", "down", "same"] | None = None
    previous: PreviousResult | None = None
```

`trend` and `previous` are both `None` or both set.

### Computation (`app/routes/home.py`, `latest_results`)

Store calls stay the same in number:
- **Today:** `keys(results:{id})`, `get(latest draw)`, `query(biomarker)`.
- **New:** `keys(results:{id})`, `query(results:{id}, start=sort_keys[-HISTORY_LIMIT])`,
  `query(biomarker)`.

1. Reuse `HISTORY_LIMIT = 30` from `app/routes/history.py`. The window is the
   member's latest ≤30 draws, which is exactly what the History chart plots.
2. The last document in the window is the latest draw. The views are built from
   it exactly as today.
3. For each result in the latest draw, walk the earlier draws newest first. The
   previous result is the first one with the same `biomarker_id` **and the same
   unit**. That's the same guard `marker_history` uses, so the previous result is
   always the chart's second-to-last dot.
4. Compare `RANK[previous.status]` with `RANK[latest.status]`: higher gives
   `"up"`, lower `"down"`, equal `"same"`.
5. If nothing is found in the window, or either status isn't in `RANK`, both
   fields are `None`.

A small pure helper keeps step 4 testable and readable:

```python
RANK = {"improve": 0, "good": 1, "optimal": 2}


def trend(previous: str, latest: str) -> str | None:
    """Band movement between two statuses; None if either is unknown."""
```

`GET /api/biomarkers` (`list_biomarkers`) shares `latest_results`, so it gets
the same fields with no extra work.

### Cost

- The fixtures have at most 6 draws: under 1 ms extra decode.
- At the 30-draw cap: about 4 ms (measured for Task 1: ~0.8 ms at 6 draws, ~4 ms
  at 30).
- The latest draw was already decoded, so the only addition is decoding up to 29
  earlier draws.

## Client

### Pill (`app/static/app.js`, `renderMarker`)

The status pill becomes: `{label}` + optional symbol + optional screen-reader
sentence.

```html
<span class="marker-status status-good">
  Good<span class="trend trend-up" aria-hidden="true"
    title="Improved from Needs work · 12 Mar 2026">↑</span><span
    class="sr-only">Improved from Needs work · 12 Mar 2026</span>
</span>
```

| `trend` | Symbol | Class | Sentence |
|---|---|---|---|
| `up` | `↑` | `trend-up` | "Improved from {previous label} · {date}" |
| `down` | `↓` | `trend-down` | "Worse than {previous label} · {date}" |
| `same` | `–` | `trend-same` | "No change from {previous label} · {date}" |
| `null` | — | — | — |

- `{previous label}` uses `BAND_LABEL` (Optimal / Good / Needs work).
- `{date}` uses `formatShortDate(previous.tested_at)`.
- The sentence is built with `textContent`, never `innerHTML`.
- **Hover:** the native `title` tooltip on the symbol.
- **Screen readers:** the `.sr-only` span. The card summary is a `<button>`, so
  the sentence becomes part of its accessible name. The symbol itself is
  `aria-hidden`.
- **Touch:** the symbol carries the direction, and the History chart shows the
  two dots.

### Colours (`app/static/styles.css`)

These are new tokens, brighter than the pill text, each at least 3:1 against
all three pill tints over the white card (WCAG 1.4.11 non-text contrast):

| Token | Value | Worst contrast vs pill tints |
|---|---|---|
| `--trend-up` | `#169443` (bright green) | 3.32:1 |
| `--trend-down` | `#e5484d` (bright red) | 3.31:1 |
| `--trend-same` | `#c2690a` (bright amber) | 3.34:1 |

- `.trend` sits a small gap after the label, bold, at the pill's font size.
- `.sr-only` is the standard visually-hidden utility (it doesn't exist in
  `styles.css` yet).
- The pill's `min-width: 5.5rem` may need to grow slightly so pills with and
  without a symbol stay aligned. This is checked by screenshot.

## Tests

New file `tests/test_trend.py`, HTTP only, against `/api/home`. It follows the
`tests/test_home.py` fixtures and posts draws through the existing ingest route.

1. **Up:** Needs work then Good gives `trend: "up"`, with `previous.status` and
   `previous.tested_at` from the first draw.
2. **Down:** Optimal then Good gives `"down"`.
3. **Same:** Good then Good with a different value gives `"same"`.
4. **First result:** a single draw gives `trend: null, previous: null`.
5. **Partial draws:** marker X in draw 1, absent from draws 2–3, present in
   draw 4. X's previous result is draw 1.
6. **Unit change:** if the previous result is in a different unit, the one before
   that (same unit) is used. If there's none, `null`.
7. **Window cap:** a previous result older than the latest 30 draws gives `null`.
8. **Isolation:** another member's draws never affect the trend.

There are also unit tests for `trend()` covering every status pair and an unknown
status. The existing `tests/test_home.py` assertions check individual fields,
not whole result dicts, so they stay green unchanged.

## Performance and verification

- `scripts/test` and `scripts/lint` pass.
- `scripts/bench.py` for all 4 members, with p95 values recorded in NOTES.md.
- Store log for one `/api/home`: still 6 calls, with the `get` replaced by one
  `query` on `results:{id}`, and no `scan`.
- Headless Playwright:
  - The pills show ↑ / ↓ / – in the right colours.
  - The `title` matches the sentence, and the `.sr-only` text is present.
  - A first-result card has no symbol.
  - The History chart's last two dots agree with the arrow for a sample marker.
  - No page errors.

## Out of scope

- Arrows on wearable cards.
- Comparing values within a band (e.g. "Good, but closer to Optimal").
- A custom tooltip component. The native `title` plus screen-reader text covers
  hover and assistive tech.
- Trends across more than 2 results.

## Assumptions to record in NOTES.md

- The trend compares **stored** statuses, as the member was told at each draw. If
  the ranges changed between draws, the arrow can move even when the value
  didn't.
- "Previous" means the marker's own previous result (same unit, within the
  latest 30 draws), not the previous draw.
- A previous result more than 30 draws back shows no arrow, consistent with the
  History chart's 30 cap.
