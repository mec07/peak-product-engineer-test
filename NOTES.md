# Notes

## Task 1: Biomarker history

Spec: [docs/superpowers/specs/2026-10-13-biomarker-history-design.md](docs/superpowers/specs/2026-10-13-biomarker-history-design.md)

### Decisions

- **History is not part of `/api/home`.** Task 2 must serve wearable charts through
  `/api/home` under the 35 ms p95 budget. Biomarker history moved to its own
  endpoint, `GET /api/biomarkers/history`, so it doesn't compete for that budget.
  `/api/home` is unchanged.
- **Lazy fetch on first expand, cached for the page.** Each marker card has a
  "History" toggle. The first expand of any card fetches history for all markers
  once; later expands are instant.
  - Considered: prefetching in the background right after the home screen renders,
    so the first expand is instant. This would not affect `scripts/bench.py`, which
    only measures `/api/home`, but it adds a second request to every page load, and
    to the dev timing panel, even when no card is opened.
  - Chose lazy fetch for simplicity and zero cost on screens where history is never
    opened. Trade-off: the first expand waits for one request.
- **One request for all markers, not one per marker.** Each draw document holds
  every marker measured that day, so one marker's history means reading every draw
  anyway. Reading once and returning all markers costs the same.
- **Capped at the latest 30 draws.** This matches the 30-day limit Task 2 uses for
  wearables. It uses `keys()`, which lists sort keys without decoding, then a range
  `query`. Measured decode cost: ~0.8 ms at 6 draws, ~4 ms at 30, ~15 ms at 100.
  `meta.truncated` tells the client when older draws exist.
- **Ranges are sent as periods, not per point.** A new period starts only when a
  marker's stored ranges change. Fixtures: 60–110 points per member collapse to
  32–36 periods, with at most 2 per marker. Status stays per point, because it's
  what the member was told at the time.
- **Charts colour the background by band.** Optimal is green, good amber and
  improve red, at about 18% opacity. Any area outside every band is red, matching
  the server's `band_for`. The tooltip also names the band in words.

- **Cards use Google's Material Web.** `md-elevated-card` (still in `labs`),
  `md-icon` and `md-ripple`, loaded from a CDN with no build step. The whole card
  summary is one button with a chevron that rotates when open, and status shows as
  a pill.
  - Considered: Web Awesome (`wa-card`/`wa-details`), Beer CSS (restyles global
    elements, so it would clash with the existing CSS), and MUI/Joy (React-only;
    Joy is on hold).
  - Risk: labs APIs can change. If the modules fail to load, the cards fall back to
    plain CSS and still work.
- **Bands at 35% opacity, with a legend** (Optimal / Good / Needs work) under each
  biomarker chart. 18% was too faint to tell the colours apart.

### Assumptions

- A point's ranges and status are as recorded at its draw. Today's definition may
  differ.
- Points whose unit differs from the marker's latest unit are left out, not
  converted. There are none in the fixtures.
- Draws are plotted evenly spaced on a category axis. That's acceptable at roughly
  6-monthly intervals and avoids a Chart.js date adapter.

### Verification

- `scripts/test`: 56 passed, 1 xfailed (the Task 2 wearable placeholder). 9 of those
  are new in `tests/test_biomarker_history.py`. `scripts/lint` is clean.
- `/api/home` p95 (code unchanged): james 28.3 ms, priya 14.3–36.4 ms, tom 32.2 ms,
  sofia 25.0 ms.
  - Priya's 36.4 ms was a one-off during a busy headless-browser session. Three
    reruns gave 20.2, 17.9 and 14.3 ms. Bench is noisy on a loaded laptop.
- `/api/biomarkers/history` (tom, 6 draws, not budgeted): p50 12.4 ms, p95 21.8 ms,
  13.8 KB.
- UI checked with headless Playwright:
  - Only `/api/me`, `/api/auth/login` and `/api/home` fire on load.
  - One history request across several card opens.
  - Signing in as another member refetches, so no data leaks between members.
  - A failed request shows "Try again", and retrying works.
  - The one-point copy shows.
  - The LDL chart shows the band step at the March 2026 range change.

### Next steps (not built)

- If draw counts grow well past 30: write a per-marker history copy at ingest,
  `history:{user_id}` with sk = `biomarker_id`. Reading one marker becomes one small
  document; the cost moves to writes in bootstrap and draw ingest.

## Task 2: Wearable history

Spec: [docs/superpowers/specs/2026-10-14-wearable-history-design.md](docs/superpowers/specs/2026-10-14-wearable-history-design.md)

### Decisions

- **A repeated delivery for the same day replaces the earlier one.** The later
  delivery is a full corrected copy with a distinct `upload_id`. In the fixtures,
  Garmin's `_r2` files correct resting HR (64→67, 58→61).
  - The replaced `upload_id` is recorded on the day as superseded. If an original
    is replayed after its correction, it is ignored (`stored: false`) and doesn't
    undo the correction.
  - Re-sending the current `upload_id` is a harmless no-op.
  - Rejected alternatives: merging field by field (a correction could never clear
    a bad value) and first-wins (it throws away the provider's corrections).
- **Only a normalised daily summary is stored, not the raw delivery.** Raw
  deliveries are ~80 KB each (1,440 per-minute HR samples). Raw data could live in
  a separate partition that `/api/home` never reads, so the budget isn't the reason.
  The reasons are size (~29 MB for the fixtures) and that the task only needs
  daily values.
  - Cost: changing how a metric is derived later means providers must resend.

### Storage layout

| Partition key | Sort key | Document |
|---|---|---|
| `wearables:{user_id}` | `YYYY-MM-DD` | `WearableDayDocument`: provider, upload_id, received_at, the 3 metrics (null when absent), superseded upload ids |

- **Cheap:** a member's latest 30 days. `keys()` (sort keys only, no decoding) finds
  the latest date, then one range `query` reads up to 30 docs of about 250 bytes.
  `/api/home` adds exactly 2 store calls.
- **Expensive or impossible:**
  - anything across members (`scan`)
  - intra-day detail (raw samples aren't kept)
  - re-deriving a metric (providers would have to resend)
- `keys()` grows by one short string per day, about 365 a year. If that ever
  matters, a per-member "latest date" pointer written at ingest replaces it.

### Other decisions

- **Missing values are gaps in the line.** No interpolation and no zero-filling.
  Each chart says "No data on N of 30 days."
- **A metric the provider doesn't supply** (Whoop steps) shows "Steps data isn't
  available from Whoop." instead of an empty chart. A supported metric with no
  values in the window says "No … recorded in the last 30 days."
- **The window ends on the member's latest delivery, not today**, as briefed. A
  late delivery for an older day never moves the window back.
- **Steps** are the sum of the 15-minute samples. Null samples are skipped, and if
  every sample is null the day has no value.
- **Whoop sleep efficiency** is a 0–1 fraction, multiplied by 100.
- **Values are normalised at ingest** (bpm, steps, %), so the client never
  converts units. Non-numeric values (strings, booleans) become null.
- **Unknown providers are rejected with a 400**, and nothing is stored.
- **Wearables sit above the biomarkers, always visible** (no expand), because the
  data already arrives with `/api/home`.

### Verification

- `scripts/test`: 75 passed, 0 xfailed.
  - 7 normaliser unit tests in `tests/test_wearable_normalise.py`.
  - 16 HTTP tests in `tests/test_wearables.py`: provider mappings, unknown
    provider, correction, replay, resend, the 30-day window with gaps and a late
    old day, isolation, no-wearables.
  - `test_home_without_draws` now also clears wearables and expects
    `"wearables": None`.
- `scripts/lint`: clean.
- After bootstrap and ingesting all 4 members, 300 day docs are stored
  (90 + 79 + 90 + 41; the two Garmin `_r2` files replaced in place).
- `/api/home` p95 (p50): james 10.2 ms (6.7), priya 8.7 ms (6.1), tom 6.7 ms
  (5.4), sofia 7.0 ms (5.5). All PASS.
- Store calls for one `/api/home` (tom):
  `get user` → `keys results:user-3` → `get` latest draw → `query biomarker` →
  `keys wearables:user-3` → `query wearables:user-3 start=2026-08-02`. No `scan`.
- Headless Playwright:
  - Oura shows 3 charts. Whoop shows gaps ("No data on 4 of 30 days") and the
    "Steps data isn't available" copy. Fitbit shows sleep gaps (11 of 30).
  - Wearable cards are cleared on sign-out.
  - An all-null mock shows the "No … recorded" copy.
  - No page errors.
  - Task 1's history walkthrough and member-isolation check still pass.

### Left out / next

- Raw sample storage and intra-day charts.
- Timezone handling beyond the provider's `calendar_date`.
- Checking `member.email` against the member record. Only `reference_id` is
  checked, as in the shell.
- Members who switch provider mid-window: `provider` and `supported` come from
  the latest day.
- Ingest auth (unchanged, as briefed).

## How I used AI tools

- Claude Code (Opus) read the brief, the store and the fixtures. It profiled the
  data: partial draws, range changes and wearable provider differences.
- I used the superpowers brainstorming → spec → plan workflow and made the product
  calls in conversation: expandable cards, colour bands, the 30 cap, ranges as
  periods, lazy fetch over prefetch.
- Claude benchmarked decode cost at 6, 30 and 100 draws on a scratch DB to ground
  the scaling decision.
- Task 2 followed the same workflow: brainstorm (you chose replace-on-repeat and
  summary-only storage), then spec, then plan, then native TDD execution.
  - Claude profiled the four providers' fixtures first: gaps, null `sleep_data`,
    Whoop's fraction and missing steps, Garmin's `_r2` corrections.
  - It verified the UI with headless Playwright screenshots and checked the
    store-call log for `/api/home`.
- Mistakes worth noting:
  - I pushed Task 1 straight to `main` once without approval. It was reverted to
    a PR, and I now work only on branches with one PR per task.
  - `ruff format` rewrites Python blocks inside the docs, which mangled one plan
    snippet. It was caught by the test run and fixed by hand.
