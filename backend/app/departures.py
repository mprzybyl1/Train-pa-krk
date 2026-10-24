"""Łączenie rozkładu z realizacją w listę najbliższych odjazdów."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from .plk.parse import LiveStop, LiveTrain, ScheduledTrip, TrainKey, short_carrier_name

# pociąg, który już odjechał, pokazujemy jeszcze chwilę (żeby nie "znikał" w trakcie patrzenia)
KEEP_DEPARTED = timedelta(minutes=2)
# najpóźniejszy pociąg, jaki jeszcze bierzemy pod uwagę (planowo odjechał dawno, ale jest opóźniony)
MAX_LOOKBACK = timedelta(hours=6)


@dataclass(frozen=True)
class Departure:
    id: str
    key: TrainKey
    planned_departure: datetime
    expected_departure: datetime
    delay: int | None  # None = brak danych na żywo
    live: bool
    departed: bool
    cancelled: bool
    category: str
    number: str
    name: str
    carrier_code: str
    carrier_name: str
    platform: str | None
    track: str | None
    planned_arrival: datetime | None
    expected_arrival: datetime | None
    arrival_delay: int | None

    def to_json(self) -> dict:
        iso = lambda d: d.isoformat() if d else None  # noqa: E731
        return {
            "id": self.id,
            "plannedDeparture": iso(self.planned_departure),
            "expectedDeparture": iso(self.expected_departure),
            "delayMinutes": self.delay,
            "live": self.live,
            "departed": self.departed,
            "cancelled": self.cancelled,
            "category": self.category,
            "number": self.number,
            "name": self.name,
            "carrierCode": self.carrier_code,
            "carrierName": self.carrier_name,
            "platform": self.platform,
            "track": self.track,
            "plannedArrival": iso(self.planned_arrival),
            "expectedArrival": iso(self.expected_arrival),
            "arrivalDelayMinutes": self.arrival_delay,
        }


def train_id(key: TrainKey) -> str:
    sid, oid, day = key
    return f"{sid}-{oid}-{day.isoformat()}"


def _delay(planned: datetime | None, actual: datetime | None, reported: int | None) -> int | None:
    """Opóźnienie w minutach: z API (withPlanned), a gdy go brak — z różnicy czasów."""
    if reported is not None:
        return reported
    if planned and actual:
        return round((actual - planned).total_seconds() / 60)
    return None


def _stop_delay(stop: LiveStop | None, planned: datetime | None, departure: bool) -> tuple[int | None, datetime | None]:
    if stop is None:
        return None, None
    if departure:
        p = stop.planned_departure or planned
        return _delay(p, stop.actual_departure, stop.departure_delay), stop.actual_departure
    p = stop.planned_arrival or planned
    return _delay(p, stop.actual_arrival, stop.arrival_delay), stop.actual_arrival


def build_board(
    trips: Iterable[ScheduledTrip],
    live: dict[TrainKey, LiveTrain],
    carriers: dict[str, str],
    now: datetime,
    window: timedelta,
    from_id: int,
    to_id: int,
) -> list[Departure]:
    out: list[Departure] = []
    seen: set[TrainKey] = set()
    for trip in trips:
        if trip.key in seen:
            continue
        if not (now - MAX_LOOKBACK <= trip.departure <= now + window):
            continue
        seen.add(trip.key)
        train = live.get(trip.key)
        origin = train.stops.get(from_id) if train else None
        dest = train.stops.get(to_id) if train else None

        delay, actual = _stop_delay(origin, trip.departure, departure=True)
        if actual is not None:
            expected = actual
        elif delay is not None:
            expected = trip.departure + timedelta(minutes=delay)
        else:
            expected = trip.departure

        departed = bool(origin and origin.confirmed)
        cancelled = bool((origin and origin.cancelled) or (train and train.status == "X"))

        cutoff = now - KEEP_DEPARTED
        if cancelled:
            if trip.departure < cutoff:
                continue
        elif expected < cutoff:
            continue

        arr_delay, arr_actual = _stop_delay(dest, trip.arrival, departure=False)
        if arr_actual is not None:
            expected_arrival = arr_actual
        elif trip.arrival and arr_delay is not None:
            expected_arrival = trip.arrival + timedelta(minutes=arr_delay)
        elif trip.arrival and delay is not None:
            expected_arrival = trip.arrival + timedelta(minutes=delay)
        else:
            expected_arrival = trip.arrival

        carrier_name = carriers.get(trip.carrier_code, trip.carrier_code)
        out.append(Departure(
            id=train_id(trip.key),
            key=trip.key,
            planned_departure=trip.departure,
            expected_departure=expected,
            delay=delay,
            live=delay is not None,
            departed=departed,
            cancelled=cancelled,
            category=trip.category,
            number=trip.number,
            name=trip.name,
            carrier_code=trip.carrier_code,
            carrier_name=short_carrier_name(carrier_name) if carrier_name else "",
            platform=trip.departure_platform,
            track=trip.departure_track,
            planned_arrival=trip.arrival,
            expected_arrival=expected_arrival,
            arrival_delay=arr_delay,
        ))
    out.sort(key=lambda d: (d.expected_departure, d.planned_departure))
    return out
