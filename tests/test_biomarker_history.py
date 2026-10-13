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
