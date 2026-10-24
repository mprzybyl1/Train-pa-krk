import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from app.worker import Worker

from .helpers import FakeSource, make_service


def client(push=False, fill=True):
    service, clock = make_service(**({"vapid_public_key": "pub", "vapid_private_key": "priv"} if push else {}))
    if fill:
        asyncio.run(Worker(service, FakeSource()).tick())
    app = create_app(settings=service.settings, store=service.store, start_worker=False)
    app.state.service.clock = clock  # ten sam zegar co w danych testowych
    return TestClient(app), clock


def test_departures_shape():
    c, _ = client()
    r = c.get("/api/departures", params={"direction": "pz-krk"})
    assert r.status_code == 200
    body = r.json()
    assert body["from"]["name"] == "Poznań Główny" and body["to"]["id"] == 80416
    assert body["stale"] is False and body["liveAgeSeconds"] == 0
    first = body["departures"][0]
    assert first["delayMinutes"] == 7 and first["platform"] == "3" and first["track"] == "6"
    assert first["carrierName"] == "PKP Intercity"
    assert r.headers["cache-control"] == "no-store"


def test_reverse_direction_and_validation():
    c, clock = client()
    clock.advance(60)
    assert [d["id"] for d in c.get("/api/departures?direction=krk-pz").json()["departures"]] == ["26-1005-2026-10-24"]
    assert c.get("/api/departures?direction=xyz").status_code == 400


def test_stale_data_keeps_departures_and_reports_age():
    c, clock = client()
    clock.advance(20 * 60)
    body = c.get("/api/departures?direction=pz-krk").json()
    assert body["stale"] is True
    assert body["liveAgeSeconds"] == 1200
    assert body["departures"]  # nie pusta lista
    assert body["notice"].startswith("Dane na żywo są nieaktualne")


def test_empty_cache_notice():
    c, _ = client(fill=False)
    body = c.get("/api/departures").json()
    assert body["departures"] == [] and body["stale"]
    assert "PLK_API_KEY" in body["notice"]


def test_health():
    c, _ = client()
    h = c.get("/api/health").json()
    assert h["ok"] and h["usage"]["requests"] == 5 and h["push"] is False


def test_push_disabled_by_default():
    c, _ = client()
    assert c.get("/api/push/config").json()["enabled"] is False
    sub = {"endpoint": "https://push.example/a", "keys": {"p256dh": "p", "auth": "a"}}
    assert c.post("/api/push/subscribe", json=sub).status_code == 503


def test_watch_flow():
    c, _ = client(push=True)
    ep = "https://push.example/a"
    assert c.post("/api/push/subscribe", json={"endpoint": ep, "keys": {"p256dh": "p", "auth": "a"}}).status_code == 200
    w = {"endpoint": ep, "trainId": "26-1001-2026-10-24", "direction": "pz-krk"}
    assert c.post("/api/watches", json=w).status_code == 200
    assert c.post("/api/watches/list", json={"endpoint": ep}).json() == {"trainIds": ["26-1001-2026-10-24"]}
    assert c.post("/api/watches", json={**w, "trainId": "nope"}).status_code == 404
    assert c.post("/api/watches/remove", json=w).status_code == 200
    assert c.post("/api/watches/list", json={"endpoint": ep}).json() == {"trainIds": []}
    assert c.post("/api/watches", json={**w, "endpoint": "https://other"}).status_code == 404
