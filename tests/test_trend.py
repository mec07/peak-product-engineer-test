"""Trend arrows: each /api/home result carries `trend` and `previous`.

The seeded dev.db already holds draws for every member. The `add_draw` fixture
clears user-1's and user-2's partitions first, and each test writes exactly the
draws it needs with the statuses it wants. Statuses are stored, not derived, so
the arrow compares what the member was told at each draw.
"""

import pytest

from app.models import BiomarkerResult, Ranges, ResultsDocument
from app.routes.home import trend
from app.store import Store

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


@pytest.fixture
def results_decoded(monkeypatch):
    """Counts the draw documents decoded from results:* partitions."""
    count = {"docs": 0}
    original = Store.query

    def counting(self, pk, *args, **kwargs):
        for item in original(self, pk, *args, **kwargs):
            if pk.startswith("results:"):
                count["docs"] += 1
            yield item

    monkeypatch.setattr(Store, "query", counting)
    return count


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


def test_resolved_trends_decode_only_the_recent_draws(
    signed_in, add_draw, results_decoded
):
    # A long-standing member: 30 draws, every marker resolved by the draw before.
    for day in range(1, 31):
        add_draw("user-1", f"2024-01-{day:02d}T09:00:00", result("a", "good"))

    assert home_by_id(signed_in)["a"]["trend"] == "same"
    assert results_decoded["docs"] == 6
