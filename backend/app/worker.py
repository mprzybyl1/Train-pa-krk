"""Worker w tle: jedyne miejsce, które odpytuje PLK.

Budżet (Basic: 100/h, 1000/dobę):
- rozkład: 2 zapytania (oba kierunki) co PLK_SCHEDULE_REFRESH_HOURS + słownik przewoźników raz na dobę,
- realizacja: 1 zapytanie co PLK_POLL_SECONDS (domyślnie 120 s -> ~720/dobę), pauza w PLK_QUIET_HOURS,
- HTTP 429: czekamy Retry-After albo rosnąco (1, 2, 4 ... maks. 15 min).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Protocol

from .plk.client import PlkError, PlkRateLimited, PlkResult
from .service import CARRIERS_KEY, DIRECTIONS, OPERATIONS_KEY, TrainService, schedule_key

log = logging.getLogger(__name__)

MAX_BACKOFF = 900.0
TICK = 15.0


class Source(Protocol):
    async def schedules(self, from_id: int, to_id: int, date_from: Any, date_to: Any) -> PlkResult: ...
    async def operations(self, station_ids: list[int]) -> PlkResult: ...
    async def carriers(self) -> PlkResult: ...
    async def aclose(self) -> None: ...


class Worker:
    def __init__(self, service: TrainService, source: Source, on_live_update=None) -> None:
        self.service = service
        self.source = source
        self.store = service.store
        self.settings = service.settings
        self.on_live_update = on_live_update  # async callable(service) — powiadomienia push
        self._backoff_until = 0.0
        self._backoff = 0.0

    # --- stan budżetu ---
    def _count_requests(self, n: int, limits: dict[str, str]) -> None:
        day = self.service.now().date().isoformat()
        usage = self.store.get_meta("usage", {})
        if usage.get("day") != day:
            usage = {"day": day, "requests": 0}
        usage["requests"] += n
        self.store.set_meta("usage", usage)
        if limits:
            self.store.set_meta("rate_limits", limits)

    def _error(self, exc: Exception) -> None:
        log.warning("PLK: %s", exc)
        self.store.set_meta("last_error", {"at": self.service.clock(), "message": str(exc)[:300]})

    def _rate_limited(self, exc: PlkRateLimited) -> None:
        self._backoff = min(MAX_BACKOFF, max(60.0, self._backoff * 2)) if exc.retry_after is None else exc.retry_after
        self._backoff_until = self.service.clock() + self._backoff
        self._error(exc)

    # --- decyzje ---
    def schedule_due(self) -> bool:
        now = self.service.clock()
        today = self.service.now().date().isoformat()
        for d in DIRECTIONS:
            entry = self.store.get(schedule_key(d))
            if entry is None:
                return True
            if now - entry.fetched_at > self.settings.schedule_refresh_hours * 3600:
                return True
        return self.store.get_meta("schedule_day") != today

    def carriers_due(self) -> bool:
        entry = self.store.get(CARRIERS_KEY)
        return entry is None or self.service.clock() - entry.fetched_at > 24 * 3600

    def operations_due(self) -> bool:
        if self.service.in_quiet_hours():
            return False
        entry = self.store.get(OPERATIONS_KEY)
        return entry is None or self.service.clock() - entry.fetched_at >= self.settings.poll_seconds - 1

    # --- pobieranie ---
    async def refresh_schedules(self) -> None:
        today = self.service.now().date()
        # wczoraj: pociągi nocne z datą kursowania z poprzedniego dnia; jutro: okno 4 h po północy
        date_from, date_to = today - timedelta(days=1), today + timedelta(days=1)
        for direction, route in self.service.routes.items():
            res = await self.source.schedules(route.from_id, route.to_id, date_from, date_to)
            self._count_requests(res.requests, res.limits)
            self.store.put(schedule_key(direction), res.body, self.service.clock())
        self.store.set_meta("schedule_day", today.isoformat())
        log.info("Rozkład odświeżony (%s – %s)", date_from, date_to)

    async def refresh_carriers(self) -> None:
        res = await self.source.carriers()
        self._count_requests(res.requests, res.limits)
        self.store.put(CARRIERS_KEY, res.body, self.service.clock())

    async def refresh_operations(self) -> None:
        res = await self.source.operations([self.settings.poznan_id, self.settings.krakow_id])
        self._count_requests(res.requests, res.limits)
        self.store.put(OPERATIONS_KEY, res.body, self.service.clock())
        self.store.set_meta("last_error", None)
        if self.on_live_update:
            try:
                await self.on_live_update(self.service)
            except Exception:  # powiadomienia nie mogą zatrzymać workera
                log.exception("Błąd powiadomień push")

    async def tick(self) -> None:
        if self.service.clock() < self._backoff_until:
            return
        try:
            if self.schedule_due():
                await self.refresh_schedules()
            if self.carriers_due():
                try:  # same nazwy przewoźników — ich brak nie może blokować opóźnień
                    await self.refresh_carriers()
                except PlkRateLimited:
                    raise
                except PlkError as exc:
                    self._error(exc)
                    self.store.put(CARRIERS_KEY, {"carriers": []}, self.service.clock())  # spróbujemy znów za dobę
            if self.operations_due():
                await self.refresh_operations()
            self._backoff = 0.0
        except PlkRateLimited as exc:
            self._rate_limited(exc)
        except PlkError as exc:
            self._error(exc)

    async def run(self) -> None:
        log.info("Worker start: realizacja co %d s, przerwa nocna %s", self.settings.poll_seconds,
                 self.settings.quiet_hours)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # nie dajemy workerowi umrzeć
                log.exception("Nieoczekiwany błąd workera")
                self._error(exc)
            await asyncio.sleep(TICK)
