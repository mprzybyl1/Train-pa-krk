from dataclasses import replace
from datetime import datetime

from app.config import Settings
from app.plk.client import PlkRateLimited, PlkResult
from app.plk.parse import WARSAW
from app.service import TrainService
from app.store import Store

from .conftest import load


class Clock:
    def __init__(self, dt: datetime) -> None:
        self.t = dt.timestamp()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeSource:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.rate_limit_next = False
        self.ops_pages = [load("operations_page1.json"), load("operations_page2.json")]

    def _maybe_limit(self):
        if self.rate_limit_next:
            self.rate_limit_next = False
            raise PlkRateLimited(retry_after=None)

    async def schedules(self, from_id, to_id, date_from, date_to):
        self.calls.append(f"schedules:{from_id}->{to_id}")
        self._maybe_limit()
        body = load("schedules_pz_krk.json")
        return PlkResult(body, {"X-RateLimit-Remaining-Day": "990"})

    async def operations(self, station_ids):
        self.calls.append("operations")
        self._maybe_limit()
        return PlkResult(self.ops_pages, {}, len(self.ops_pages))

    async def carriers(self):
        self.calls.append("carriers")
        return PlkResult(load("carriers.json"))

    async def aclose(self):
        pass


def make_service(now=datetime(2026, 10, 24, 13, 30, tzinfo=WARSAW), **overrides):
    settings = replace(Settings(), static_dir="", worker_enabled=False, **overrides)
    clock = Clock(now)
    return TrainService(settings, Store(":memory:"), clock), clock
