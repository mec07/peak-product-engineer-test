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
