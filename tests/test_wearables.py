"""Starting point for the task 2 tests.

These go through HTTP only and say nothing about how deliveries are stored;
that layout is yours to choose. The xfail below fails today because the endpoint
does not persist. Remove the marker once it does, then add tests for provider
differences, missing values, repeated deliveries and the history response.
"""

import json
from pathlib import Path

import pytest

WEARABLES = Path(__file__).resolve().parents[1] / "data" / "wearables"

EMAILS = {
    "user-1": "james.chen@example.com",
    "user-2": "priya.raman@example.com",
    "user-3": "tom.okafor@example.com",
    "user-4": "sofia.marek@example.com",
}


def delivery(user_id, name):
    """One fixture file, e.g. delivery("user-3", "2026-07-17_r2")."""
    return json.loads((WEARABLES / user_id / f"{name}.json").read_text())


def sign_in(client, user_id):
    response = client.post("/api/auth/login", json={"email": EMAILS[user_id]})
    assert response.status_code == 200
    return client


@pytest.fixture(autouse=True)
def no_wearables(store):
    """dev.db may already hold imported deliveries; every test starts empty."""
    for user_id in EMAILS:
        partition = f"wearables:{user_id}"
        for key in list(store.keys(partition)):
            store.delete(partition, key)


def ingest(client, user_id, name):
    response = client.post(
        f"/api/users/{user_id}/wearables", json=delivery(user_id, name)
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_ingest_stores_a_delivery(client):
    response = client.post(
        "/api/users/user-1/wearables", json=delivery("user-1", "2026-08-31")
    )
    assert response.status_code == 200
    assert response.json()["data"]["stored"] is True


def test_home_after_ingest(client):
    client.post("/api/users/user-1/wearables", json=delivery("user-1", "2026-08-31"))
    response = sign_in(client, "user-1").get("/api/home")
    assert response.status_code == 200
    assert set(response.json()) == {"data", "meta"}


def test_unknown_provider_is_rejected(client):
    body = delivery("user-1", "2026-08-31")
    body["provider"] = "polar"
    response = client.post("/api/users/user-1/wearables", json=body)
    assert response.status_code == 400
    assert response.json()["detail"] == "Unsupported provider: polar"


def test_a_correction_replaces_the_earlier_delivery(client):
    first = ingest(client, "user-3", "2026-07-17")
    assert first == {
        "stored": True,
        "date": "2026-07-17",
        "replaced_upload_id": None,
    }
    second = ingest(client, "user-3", "2026-07-17_r2")
    assert second == {
        "stored": True,
        "date": "2026-07-17",
        "replaced_upload_id": "ga_20260717",
    }


def test_a_replayed_original_after_its_correction_is_ignored(client):
    ingest(client, "user-3", "2026-07-17")
    ingest(client, "user-3", "2026-07-17_r2")
    assert ingest(client, "user-3", "2026-07-17")["stored"] is False


def test_resending_the_same_delivery_is_a_no_op(client):
    assert ingest(client, "user-1", "2026-08-31")["stored"] is True
    again = ingest(client, "user-1", "2026-08-31")
    assert again == {"stored": True, "date": "2026-08-31", "replaced_upload_id": None}


def home_wearables(client, user_id):
    response = sign_in(client, user_id).get("/api/home")
    assert response.status_code == 200
    return response.json()["data"]["wearables"]


def series(wearables, metric):
    return next(s for s in wearables["series"] if s["metric"] == metric)


def value_on(wearables, metric, day):
    points = series(wearables, metric)["points"]
    return next(p["value"] for p in points if p["date"] == day)


def test_home_without_wearables_has_none(client):
    assert home_wearables(client, "user-1") is None


def test_oura_day_reaches_home_normalised(client):
    ingest(client, "user-1", "2026-08-31")
    raw = delivery("user-1", "2026-08-31")["data"]
    w = home_wearables(client, "user-1")
    assert w["provider"] == "oura"
    assert [(s["metric"], s["label"], s["unit"]) for s in w["series"]] == [
        ("resting_hr_bpm", "Resting heart rate", "bpm"),
        ("steps", "Steps", "steps"),
        ("sleep_efficiency_pct", "Sleep efficiency", "%"),
    ]
    assert (
        value_on(w, "resting_hr_bpm", "2026-08-31")
        == (raw["heart_rate_data"]["resting_hr_bpm"])
    )
    assert value_on(w, "steps", "2026-08-31") == sum(
        s["value"] for s in raw["activity_data"]["steps_samples"]
    )
    assert (
        value_on(w, "sleep_efficiency_pct", "2026-08-31")
        == (raw["sleep_data"]["efficiency_pct"])
    )


def test_whoop_steps_are_unsupported_and_sleep_is_a_percentage(client):
    ingest(client, "user-2", "2026-08-31")
    raw = delivery("user-2", "2026-08-31")["data"]
    w = home_wearables(client, "user-2")
    steps = series(w, "steps")
    assert steps["supported"] is False
    assert all(p["value"] is None for p in steps["points"])
    assert value_on(w, "sleep_efficiency_pct", "2026-08-31") == round(
        raw["sleep"]["efficiency"] * 100, 1
    )


def test_fitbit_null_sleep_is_a_gap_but_other_metrics_remain(client):
    name = next(
        p.stem
        for p in sorted((WEARABLES / "user-4").glob("*.json"))
        if json.loads(p.read_text())["data"]["sleep_data"] is None
    )
    ingest(client, "user-4", name)
    w = home_wearables(client, "user-4")
    assert value_on(w, "sleep_efficiency_pct", name) is None
    assert value_on(w, "resting_hr_bpm", name) is not None
    assert value_on(w, "steps", name) is not None


def test_correction_shows_on_home_and_survives_a_replay(client):
    ingest(client, "user-3", "2026-07-17")
    ingest(client, "user-3", "2026-07-17_r2")
    ingest(client, "user-3", "2026-07-17")
    w = home_wearables(client, "user-3")
    assert value_on(w, "resting_hr_bpm", "2026-07-17") == 67


def test_window_is_thirty_days_ending_on_latest_delivery(client):
    present = sorted(
        p.stem
        for p in (WEARABLES / "user-2").glob("*.json")
        if "2026-08-15" <= p.stem <= "2026-08-31"
    )
    for name in present:
        ingest(client, "user-2", name)
    ingest(client, "user-2", "2026-07-01")  # late, and older than the window

    w = home_wearables(client, "user-2")
    assert (w["start"], w["end"]) == ("2026-08-02", "2026-08-31")
    dates = [p["date"] for p in series(w, "resting_hr_bpm")["points"]]
    assert len(dates) == 30
    assert dates[0] == "2026-08-02"
    assert dates[-1] == "2026-08-31"
    assert "2026-07-01" not in dates
    assert value_on(w, "resting_hr_bpm", "2026-08-16") is None  # no delivery
    assert value_on(w, "resting_hr_bpm", "2026-08-10") is None  # not ingested
    assert value_on(w, "resting_hr_bpm", "2026-08-31") is not None


def test_wearables_are_isolated_per_member(client):
    ingest(client, "user-1", "2026-08-31")
    assert home_wearables(client, "user-2") is None
    assert home_wearables(client, "user-1")["provider"] == "oura"
