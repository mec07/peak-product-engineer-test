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

### Assumptions

- A point's ranges and status are as recorded at its draw. Today's definition may
  differ.
- Points whose unit differs from the marker's latest unit are left out, not
  converted. There are none in the fixtures.
- Draws are plotted evenly spaced on a category axis. That's acceptable at roughly
  6-monthly intervals and avoids a Chart.js date adapter.

### Next steps (not built)

- If draw counts grow well past 30: write a per-marker history copy at ingest,
  `history:{user_id}` with sk = `biomarker_id`. Reading one marker becomes one small
  document; the cost moves to writes in bootstrap and draw ingest.

## Task 2: Wearable history

Not started.

## How I used AI tools

- Claude Code (Opus) read the brief, the store and the fixtures. It profiled the
  data: partial draws, range changes and wearable provider differences.
- I used the superpowers brainstorming → spec → plan workflow and made the product
  calls in conversation: expandable cards, colour bands, the 30 cap, ranges as
  periods, lazy fetch over prefetch.
- Claude benchmarked decode cost at 6, 30 and 100 draws on a scratch DB to ground
  the scaling decision.
