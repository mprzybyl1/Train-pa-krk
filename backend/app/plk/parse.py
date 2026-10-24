"""Parsowanie odpowiedzi API PLK (pełne nazwy pól, OpenAPI v1.1.4) do prostych modeli.

Czyste funkcje bez I/O — testowane na zapisanych JSON-ach w tests/fixtures/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

TrainKey = tuple[int, int, date]  # (scheduleId, orderId, operatingDate)


# --- czas -------------------------------------------------------------------

_FRACTION = re.compile(r"(\.\d{6})\d+")
_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_datetime(value: Any) -> datetime | None:
    """Data i czas z API -> datetime świadomy strefy (Europe/Warsaw).

    Czasy pociągów są lokalne bez strefy („UTC nieoznaczone” w spec.), więc naiwne
    wartości traktujemy jako czas warszawski. Wartości z 'Z'/offsetem przeliczamy.
    """
    if not value or not isinstance(value, str):
        return None
    text = _FRACTION.sub(r"\1", value.strip()).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=WARSAW)
    return dt.astimezone(WARSAW)


def parse_timespan(value: Any) -> timedelta | None:
    """TimeSpan z .NET: '14:05:00', '14:05', '1.02:05:00' albo ISO 'PT14H5M'."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if m := _ISO_DURATION.match(text):
        d, h, mi, s = m.groups()
        return timedelta(days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=float(s or 0))
    days = 0
    if "." in text.split(":")[0]:
        d, _, text = text.partition(".")
        days = int(d)
    parts = text.split(":")
    try:
        h, mi = int(parts[0]), int(parts[1])
        s = float(parts[2]) if len(parts) > 2 else 0.0
    except (IndexError, ValueError):
        return None
    return timedelta(days=days, hours=h, minutes=mi, seconds=s)


def local_at(day: date, offset_days: int | None, span: timedelta | None) -> datetime | None:
    """Data kursowania + przesunięcie dni + godzina -> moment w Europe/Warsaw."""
    if span is None:
        return None
    naive = datetime.combine(day, datetime.min.time()) + timedelta(days=offset_days or 0) + span
    return naive.replace(tzinfo=WARSAW)


def parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# --- słowniki -----------------------------------------------------------------

def _name_map(raw: Any) -> dict[str, str]:
    """Słownik z API do {klucz: nazwa}. Spec opisuje go tylko jako 'object', więc
    akceptujemy {id: 'nazwa'}, {id: {'name': ...}} i listę [{'id'|'code': ..., 'name': ...}]."""
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, str):
                out[str(k)] = v
            elif isinstance(v, dict):
                out[str(k)] = str(v.get("name") or v.get("nm") or k)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                k = item.get("id", item.get("code"))
                if k is not None:
                    out[str(k)] = str(item.get("name") or k)
    return out


def parse_carriers(body: Any) -> dict[str, str]:
    """/api/v1/dictionaries/carriers -> {kod: pełna nazwa}."""
    if not isinstance(body, dict):
        return {}
    return _name_map(body.get("carriers"))


def short_carrier_name(name: str) -> str:
    """'„PKP Intercity” Spółka Akcyjna' -> 'PKP Intercity'."""
    name = re.sub(r"\s*\(.*?\)", "", name)
    name = re.sub(r"\s+(spółka akcyjna|s\.?a\.?|sp\. ?z ?o\.? ?o\.?)\s*$", "", name.strip(), flags=re.I)
    return name.strip("„”\"' ").strip()


# --- rozkład ------------------------------------------------------------------

@dataclass(frozen=True)
class ScheduledTrip:
    """Jeden kurs pociągu w konkretnym dniu, przycięty do odcinka stacja A -> stacja B."""

    schedule_id: int
    order_id: int
    operating_date: date
    carrier_code: str
    category: str
    number: str
    name: str
    departure: datetime  # planowy odjazd ze stacji początkowej (Warszawa TZ)
    departure_platform: str | None
    departure_track: str | None
    arrival: datetime | None  # planowy przyjazd do stacji docelowej
    arrival_platform: str | None = None
    arrival_track: str | None = None

    @property
    def key(self) -> TrainKey:
        return (self.schedule_id, self.order_id, self.operating_date)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _train_number(route: dict, stop: dict) -> str:
    for candidate in (stop.get("departureTrainNumber"), route.get("nationalNumber"),
                      route.get("internationalDepartureNumber"), route.get("internationalArrivalNumber")):
        if text := _clean(candidate):
            return text.lstrip("0") or text
    return ""


def parse_schedules(body: Any, from_id: int, to_id: int) -> list[ScheduledTrip]:
    """/api/v1/schedules -> kursy z from_id do to_id (po jednym na każdą datę kursowania).

    Kierunek sprawdzamy sami (orderNumber stacji A < stacji B), nawet jeśli zapytanie
    używało fromStations/toStations — tani bezpiecznik na wypadek zmian w API.
    """
    if not isinstance(body, dict):
        return []
    trips: list[ScheduledTrip] = []
    for route in body.get("routes") or []:
        stops = route.get("stations") or []
        origin = next((s for s in stops if s.get("stationId") == from_id), None)
        dest = next((s for s in stops if s.get("stationId") == to_id), None)
        if origin is None or dest is None:
            continue
        if (origin.get("orderNumber") or 0) >= (dest.get("orderNumber") or 0):
            continue
        dep_span = parse_timespan(origin.get("departureTime"))
        if dep_span is None:  # na stacji A pociąg tylko kończy bieg / nie zabiera pasażerów
            continue
        arr_span = parse_timespan(dest.get("arrivalTime") or dest.get("departureTime"))
        category = _clean(origin.get("departureCommercialCategory")) or _clean(route.get("commercialCategorySymbol")) or ""
        for raw_day in route.get("operatingDates") or []:
            day = parse_date(raw_day)
            if day is None:
                continue
            departure = local_at(day, origin.get("departureDay"), dep_span)
            assert departure is not None
            trips.append(ScheduledTrip(
                schedule_id=int(route.get("scheduleId") or 0),
                order_id=int(route.get("orderId") or 0),
                operating_date=day,
                carrier_code=_clean(route.get("carrierCode")) or "",
                category=category,
                number=_train_number(route, origin),
                name=_clean(route.get("name")) or "",
                departure=departure,
                departure_platform=_clean(origin.get("departurePlatform")),
                departure_track=_clean(origin.get("departureTrack")),
                arrival=local_at(day, dest.get("arrivalDay") if dest.get("arrivalTime") else dest.get("departureDay"), arr_span),
                arrival_platform=_clean(dest.get("arrivalPlatform")),
                arrival_track=_clean(dest.get("arrivalTrack")),
            ))
    trips.sort(key=lambda t: t.departure)
    return trips


def schedule_carrier_names(body: Any) -> dict[str, str]:
    """Nazwy przewoźników ze słownika dołączonego do /schedules (dictionaries=true)."""
    if not isinstance(body, dict):
        return {}
    return _name_map((body.get("dictionaries") or {}).get("carriers"))


# --- realizacja -------------------------------------------------------------------

@dataclass(frozen=True)
class LiveStop:
    station_id: int
    planned_arrival: datetime | None
    planned_departure: datetime | None
    actual_arrival: datetime | None  # rzeczywisty albo prognozowany (gdy confirmed=False)
    actual_departure: datetime | None
    arrival_delay: int | None
    departure_delay: int | None
    confirmed: bool
    cancelled: bool


@dataclass(frozen=True)
class LiveTrain:
    schedule_id: int
    order_id: int
    operating_date: date
    status: str  # S, P, C, X, Q
    stops: dict[int, LiveStop] = field(default_factory=dict)

    @property
    def key(self) -> TrainKey:
        return (self.schedule_id, self.order_id, self.operating_date)


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_operations(pages: Iterable[Any]) -> dict[TrainKey, LiveTrain]:
    """Strony z /api/v1/operations?withPlanned=true -> {klucz pociągu: LiveTrain}."""
    trains: dict[TrainKey, LiveTrain] = {}
    for body in pages:
        if not isinstance(body, dict):
            continue
        for t in body.get("trains") or []:
            day = parse_date(t.get("operatingDate"))
            if day is None:
                continue
            stops: dict[int, LiveStop] = {}
            for s in t.get("stations") or []:
                sid = _int_or_none(s.get("stationId"))
                if sid is None:
                    continue
                stops[sid] = LiveStop(
                    station_id=sid,
                    planned_arrival=parse_datetime(s.get("plannedArrival")),
                    planned_departure=parse_datetime(s.get("plannedDeparture")),
                    actual_arrival=parse_datetime(s.get("actualArrival")),
                    actual_departure=parse_datetime(s.get("actualDeparture")),
                    arrival_delay=_int_or_none(s.get("arrivalDelayMinutes")),
                    departure_delay=_int_or_none(s.get("departureDelayMinutes")),
                    confirmed=bool(s.get("isConfirmed")),
                    cancelled=bool(s.get("isCancelled")),
                )
            train = LiveTrain(
                schedule_id=int(t.get("scheduleId") or 0),
                order_id=int(t.get("orderId") or 0),
                operating_date=day,
                status=str(t.get("trainStatus") or ""),
                stops=stops,
            )
            trains[train.key] = train
    return trains


def has_next_page(body: Any) -> bool:
    return bool(isinstance(body, dict) and (body.get("pagination") or {}).get("hasNextPage"))
