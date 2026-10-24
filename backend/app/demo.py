"""Tryb demo (DEMO_MODE=true): syntetyczne dane w formacie odpowiedzi PLK, liczone względem "teraz".

Służy do podglądu interfejsu bez klucza API. Pociągi, numery i opóźnienia są zmyślone,
a UI wyświetla wyraźny znacznik DEMO.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from .plk.client import PlkResult
from .plk.parse import WARSAW

# (kategoria, numer bazowy, nazwa, przewoźnik, godzina odjazdu z Poznania, czas jazdy w min)
_PZ_KRK = [
    ("IC", "DEMO 101", "", "IC", "05:12", 340),
    ("TLK", "DEMO 103", "Demo Jeden", "IC", "06:40", 375),
    ("IC", "DEMO 105", "Demo Dwa", "IC", "08:05", 330),
    ("REG", "DEMO 107", "", "PR", "09:30", 420),
    ("IC", "DEMO 109", "Demo Trzy", "IC", "10:55", 335),
    ("TLK", "DEMO 111", "", "IC", "12:20", 370),
    ("IC", "DEMO 113", "Demo Cztery", "IC", "13:45", 330),
    ("IC", "DEMO 115", "", "IC", "15:10", 340),
    ("TLK", "DEMO 117", "Demo Pięć", "IC", "16:35", 365),
    ("IC", "DEMO 119", "Demo Sześć", "IC", "18:00", 330),
    ("IC", "DEMO 121", "", "IC", "19:25", 345),
    ("TLK", "DEMO 123", "Demo Nocny", "IC", "21:50", 400),
    ("TLK", "DEMO 125", "Demo Późny", "IC", "23:30", 410),
]
_KRK_PZ = [(c, n.replace("1", "2", 1), nm, cr, t, d) for c, n, nm, cr, t, d in _PZ_KRK]
_PLATFORMS = ["1", "2", "3", "4", "5", None]


def _h(*parts: object) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def _fmt(dt: datetime) -> str:
    return dt.replace(tzinfo=None).isoformat(timespec="seconds")


class DemoSource:
    def __init__(self, poznan_id: int, krakow_id: int, clock=None) -> None:
        self.poznan_id = poznan_id
        self.krakow_id = krakow_id
        self.clock = clock or (lambda: datetime.now(WARSAW))

    def _table(self, from_id: int):
        return _PZ_KRK if from_id == self.poznan_id else _KRK_PZ

    async def schedules(self, from_id: int, to_id: int, date_from: date, date_to: date) -> PlkResult:
        days = [(date_from + timedelta(days=i)).isoformat() for i in range((date_to - date_from).days + 1)]
        routes = []
        for i, (cat, num, name, carrier, hhmm, minutes) in enumerate(self._table(from_id)):
            h, m = map(int, hhmm.split(":"))
            dep = timedelta(hours=h, minutes=m)
            arr = dep + timedelta(minutes=minutes)
            plat = _PLATFORMS[_h(num, "p") % len(_PLATFORMS)]
            routes.append({
                "scheduleId": 26, "orderId": 900000 + i + (0 if from_id == self.poznan_id else 100),
                "name": name or None, "carrierCode": carrier, "nationalNumber": num,
                "commercialCategorySymbol": cat, "operatingDates": days,
                "stations": [
                    {"stationId": from_id, "orderNumber": 1, "departureTime": str(dep).zfill(8),
                     "departureDay": 0, "departurePlatform": plat,
                     "departureTrack": str(_h(num, "t") % 12 + 1) if plat else None,
                     "departureCommercialCategory": cat},
                    {"stationId": to_id, "orderNumber": 2, "arrivalTime": str(timedelta(seconds=arr.seconds)).zfill(8),
                     "arrivalDay": arr.days, "arrivalPlatform": "2", "arrivalTrack": "4"},
                ],
            })
        return PlkResult({"generatedAt": _fmt(self.clock()) + "Z", "routes": routes,
                          "dictionaries": {"carriers": {"IC": "PKP Intercity", "PR": "POLREGIO"}}})

    async def operations(self, station_ids: list[int]) -> PlkResult:
        now = self.clock()
        bucket = int(now.timestamp() // 600)  # opóźnienia zmieniają się co 10 min
        trains = []
        for from_id, to_id in ((self.poznan_id, self.krakow_id), (self.krakow_id, self.poznan_id)):
            for i, (cat, num, _n, _c, hhmm, minutes) in enumerate(self._table(from_id)):
                for day in (now.date() - timedelta(days=1), now.date()):
                    h, m = map(int, hhmm.split(":"))
                    planned = datetime.combine(day, datetime.min.time(), WARSAW) + timedelta(hours=h, minutes=m)
                    if not (now - timedelta(hours=1) <= planned <= now + timedelta(hours=5)):
                        continue
                    r = _h(num, day, bucket) % 100
                    delay = 0 if r < 45 else (r % 12 + 1) if r < 85 else (r % 40 + 16)
                    cancelled = _h(num, day) % 23 == 0
                    actual = planned + timedelta(minutes=delay)
                    arr = planned + timedelta(minutes=minutes)
                    trains.append({
                        "scheduleId": 26, "orderId": 900000 + i + (0 if from_id == self.poznan_id else 100),
                        "operatingDate": day.isoformat(),
                        "trainStatus": "X" if cancelled else ("P" if actual <= now else "S"),
                        "stations": [
                            {"stationId": from_id, "plannedDeparture": _fmt(planned),
                             "actualDeparture": _fmt(actual),
                             "departureDelayMinutes": delay or None,
                             "isConfirmed": actual <= now, "isCancelled": cancelled},
                            {"stationId": to_id, "plannedArrival": _fmt(arr),
                             "actualArrival": _fmt(arr + timedelta(minutes=delay)),
                             "arrivalDelayMinutes": delay or None, "isConfirmed": False, "isCancelled": cancelled},
                        ],
                    })
        return PlkResult([{"generatedAt": _fmt(now) + "Z", "trains": trains,
                           "pagination": {"page": 1, "hasNextPage": False}}])

    async def carriers(self) -> PlkResult:
        return PlkResult({"carriers": [{"code": "IC", "name": "„PKP Intercity” Spółka Akcyjna"},
                                       {"code": "PR", "name": "POLREGIO S.A."}]})

    async def aclose(self) -> None:
        return None
