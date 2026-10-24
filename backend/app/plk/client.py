"""Klient HTTP do Otwarte Dane Kolejowe (PKP PLK). Wywoływany wyłącznie z workera."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from .parse import has_next_page

log = logging.getLogger(__name__)

MAX_OPERATION_PAGES = 5
RETRY_DELAYS = (5.0, 20.0)


class PlkError(RuntimeError):
    pass


class PlkRateLimited(PlkError):
    def __init__(self, retry_after: float | None, message: str = "") -> None:
        super().__init__(message or "PLK: przekroczony limit zapytań (HTTP 429)")
        self.retry_after = retry_after


@dataclass
class PlkResult:
    body: Any
    limits: dict[str, str] = field(default_factory=dict)
    requests: int = 1


def _limits(headers: httpx.Headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower().startswith("x-ratelimit") or k.lower() == "retry-after"}


class PlkClient:
    def __init__(self, api_key: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 retry_delays: tuple[float, ...] = RETRY_DELAYS) -> None:
        if not api_key:
            raise PlkError("Brak PLK_API_KEY")
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"X-API-Key": api_key, "Accept": "application/json", "User-Agent": "pociagi-pz-krk/1.0"},
            timeout=httpx.Timeout(60.0, connect=10.0),
            transport=transport,
        )
        self._retry_delays = retry_delays

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> PlkResult:
        attempts = (*self._retry_delays, None)
        for attempt, wait in enumerate(attempts, 1):
            try:
                resp = await self._http.get(path, params=params)
            except httpx.TransportError as exc:
                if wait is None:
                    raise PlkError(f"{path}: {type(exc).__name__}: {exc}") from exc
                log.warning("%s: %s, próba %d, ponawiam za %.0f s", path, type(exc).__name__, attempt, wait)
                await asyncio.sleep(wait)
                continue
            limits = _limits(resp.headers)
            if resp.status_code == 429:
                retry = resp.headers.get("Retry-After")
                raise PlkRateLimited(float(retry) if retry and retry.isdigit() else None)
            if resp.status_code >= 500 and wait is not None:
                log.warning("%s: HTTP %d, próba %d, ponawiam za %.0f s", path, resp.status_code, attempt, wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code != 200:
                raise PlkError(f"{path}: HTTP {resp.status_code} {resp.text[:300]}")
            try:
                return PlkResult(resp.json(), limits, attempt)
            except ValueError as exc:
                raise PlkError(f"{path}: niepoprawny JSON") from exc
        raise AssertionError("unreachable")

    async def schedules(self, from_id: int, to_id: int, date_from: date, date_to: date) -> PlkResult:
        return await self.get("/api/v1/schedules", {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "fromStations": str(from_id),
            "toStations": str(to_id),
            "dictionaries": "true",
        })

    async def operations(self, station_ids: list[int]) -> PlkResult:
        """Wszystkie strony realizacji dla podanych stacji. Zwraca listę stron w .body."""
        pages: list[Any] = []
        limits: dict[str, str] = {}
        requests = 0
        for page in range(1, MAX_OPERATION_PAGES + 1):
            res = await self.get("/api/v1/operations", {
                "stations": ",".join(map(str, station_ids)),
                "withPlanned": "true",
                "pageSize": "5000",
                "page": str(page),
            })
            pages.append(res.body)
            limits = res.limits or limits
            requests += res.requests
            if not has_next_page(res.body):
                break
        return PlkResult(pages, limits, requests)

    async def carriers(self) -> PlkResult:
        return await self.get("/api/v1/dictionaries/carriers")
