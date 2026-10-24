"""Warstwa między cache (SQLite) a API: parsowanie z pamięcią podręczną i budowa odpowiedzi."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from .config import Settings
from .departures import Departure, build_board
from .plk.parse import (
    WARSAW,
    LiveTrain,
    ScheduledTrip,
    TrainKey,
    parse_carriers,
    parse_operations,
    parse_schedules,
    schedule_carrier_names,
)
from .store import Store

DIRECTIONS = ("pz-krk", "krk-pz")
OPERATIONS_KEY = "operations"
CARRIERS_KEY = "carriers"


def schedule_key(direction: str) -> str:
    return f"schedules:{direction}"


@dataclass(frozen=True)
class Route:
    direction: str
    from_id: int
    to_id: int
    from_name: str
    to_name: str


def routes(settings: Settings) -> dict[str, Route]:
    return {
        "pz-krk": Route("pz-krk", settings.poznan_id, settings.krakow_id, "Poznań Główny", "Kraków Główny"),
        "krk-pz": Route("krk-pz", settings.krakow_id, settings.poznan_id, "Kraków Główny", "Poznań Główny"),
    }


class _Memo:
    """Parsuje wpis z cache tylko wtedy, gdy zmienił się jego fetched_at."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, store: Store, key: str, parse: Callable[[Any], Any], empty: Any) -> tuple[Any, float | None]:
        entry = store.get(key)
        if entry is None:
            return empty, None
        cached = self._data.get(key)
        if cached and cached[0] == entry.fetched_at:
            return cached[1], entry.fetched_at
        value = parse(entry.body)
        self._data[key] = (entry.fetched_at, value)
        return value, entry.fetched_at


class TrainService:
    def __init__(self, settings: Settings, store: Store, clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self.store = store
        self.clock = clock
        self.routes = routes(settings)
        self._memo = _Memo()

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), WARSAW)

    def trips(self, direction: str) -> tuple[list[ScheduledTrip], float | None]:
        r = self.routes[direction]
        return self._memo.get(self.store, schedule_key(direction),
                              lambda b: parse_schedules(b, r.from_id, r.to_id), [])

    def live(self) -> tuple[dict[TrainKey, LiveTrain], float | None]:
        return self._memo.get(self.store, OPERATIONS_KEY, parse_operations, {})

    def carriers(self) -> dict[str, str]:
        names: dict[str, str] = {}
        for direction in DIRECTIONS:
            entry = self.store.get(schedule_key(direction))
            if entry:
                names.update(schedule_carrier_names(entry.body))
        full, _ = self._memo.get(self.store, CARRIERS_KEY, parse_carriers, {})
        names.update(full)
        return names

    def board(self, direction: str, now: datetime | None = None) -> list[Departure]:
        r = self.routes[direction]
        now = now or self.now()
        trips, _ = self.trips(direction)
        live, _ = self.live()
        return build_board(trips, live, self.carriers(), now,
                           timedelta(hours=self.settings.window_hours), r.from_id, r.to_id)

    def in_quiet_hours(self, now: datetime | None = None) -> bool:
        q = self.settings.quiet_hours
        if not q:
            return False
        t = (now or self.now()).time()
        start, end = q
        return start <= t < end if start <= end else (t >= start or t < end)

    def departures_response(self, direction: str) -> dict:
        r = self.routes[direction]
        now = self.now()
        board = self.board(direction, now)
        _, sched_at = self.trips(direction)
        _, ops_at = self.live()
        ts = self.clock()
        age = round(ts - ops_at) if ops_at else None
        stale = age is None or age > self.settings.stale_after_seconds

        notice = None
        if sched_at is None:
            notice = ("Brak klucza PLK_API_KEY — backend nie pobiera danych."
                      if not (self.settings.plk_api_key or self.settings.demo)
                      else "Czekam na pierwsze pobranie rozkładu z PLK.")
        elif stale and self.in_quiet_hours(now):
            notice = "Przerwa nocna — dane na żywo wrócą rano."
        elif stale:
            err = self.store.get_meta("last_error")
            notice = "Dane na żywo są nieaktualne." + (f" Ostatni błąd: {err['message']}" if err else "")

        return {
            "direction": direction,
            "from": {"id": r.from_id, "name": r.from_name},
            "to": {"id": r.to_id, "name": r.to_name},
            "now": now.isoformat(),
            "windowHours": self.settings.window_hours,
            "scheduleFetchedAt": _iso(sched_at),
            "liveFetchedAt": _iso(ops_at),
            "liveAgeSeconds": age,
            "stale": stale,
            "demo": self.settings.demo,
            "notice": notice,
            "departures": [d.to_json() for d in board],
        }


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, WARSAW).isoformat() if ts else None
