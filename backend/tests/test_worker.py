import asyncio
from datetime import datetime, time

from app.plk.parse import WARSAW
from app.worker import Worker

from .helpers import FakeSource, make_service


def run(coro):
    return asyncio.run(coro)


def test_first_tick_fetches_everything_then_respects_interval():
    service, clock = make_service(poll_seconds=120)
    src = FakeSource()
    w = Worker(service, src)
    run(w.tick())
    assert src.calls == ["schedules:30601->80416", "schedules:80416->30601", "carriers", "operations"]
    src.calls.clear()
    clock.advance(60)
    run(w.tick())
    assert src.calls == []  # za wcześnie na kolejne operations
    clock.advance(60)
    run(w.tick())
    assert src.calls == ["operations"]
    usage = service.store.get_meta("usage")
    assert usage == {"day": "2026-10-24", "requests": 2 + 1 + 2 + 2}


def test_schedule_refreshed_on_new_day():
    service, clock = make_service(poll_seconds=120)
    src = FakeSource()
    w = Worker(service, src)
    run(w.tick())
    src.calls.clear()
    clock.advance(11 * 3600)  # 00:30 następnego dnia
    run(w.tick())
    assert "schedules:30601->80416" in src.calls


def test_quiet_hours_skip_operations():
    service, clock = make_service(now=datetime(2026, 10, 24, 2, 0, tzinfo=WARSAW),
                                  quiet_hours=(time(1, 0), time(4, 30)))
    src = FakeSource()
    run(Worker(service, src).tick())
    assert "operations" not in src.calls
    assert service.departures_response("pz-krk")["notice"].startswith("Przerwa nocna")


def test_rate_limit_backs_off():
    service, clock = make_service()
    src = FakeSource()
    w = Worker(service, src)
    src.rate_limit_next = True
    run(w.tick())
    assert service.store.get_meta("last_error")["message"].startswith("PLK: przekroczony limit")
    src.calls.clear()
    clock.advance(30)
    run(w.tick())
    assert src.calls == []  # czekamy min. 60 s
    clock.advance(31)
    run(w.tick())
    assert "operations" in src.calls


def test_live_update_hook_called():
    service, _ = make_service()
    seen = []

    async def hook(svc):
        seen.append(svc)

    run(Worker(service, FakeSource(), hook).tick())
    assert seen == [service]
