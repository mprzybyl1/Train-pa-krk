from datetime import date, datetime, timedelta

from app.plk.parse import (
    WARSAW,
    parse_carriers,
    parse_datetime,
    parse_operations,
    parse_schedules,
    parse_timespan,
    schedule_carrier_names,
    short_carrier_name,
)

PZ, KRK = 30601, 80416


def test_timespan_formats():
    assert parse_timespan("14:05:00") == timedelta(hours=14, minutes=5)
    assert parse_timespan("14:05") == timedelta(hours=14, minutes=5)
    assert parse_timespan("1.01:05:00") == timedelta(days=1, hours=1, minutes=5)
    assert parse_timespan("PT19H20M") == timedelta(hours=19, minutes=20)
    assert parse_timespan(None) is None
    assert parse_timespan("bzdura") is None


def test_datetime_naive_is_warsaw_local():
    dt = parse_datetime("2026-10-24T14:05:00")
    assert dt == datetime(2026, 10, 24, 14, 5, tzinfo=WARSAW)
    assert dt.utcoffset() == timedelta(hours=2)  # CEST


def test_datetime_utc_with_7_digit_fraction_is_converted():
    dt = parse_datetime("2026-10-24T12:00:01.6582106Z")
    assert (dt.hour, dt.minute, dt.second) == (14, 0, 1)
    assert dt.tzinfo == WARSAW


def test_datetime_winter_time_after_dst_change():
    # 25.10.2026 o 3:00 CEST -> 2:00 CET
    assert parse_datetime("2026-10-26T08:00:00").utcoffset() == timedelta(hours=1)


def test_schedules_direction_and_skips(schedules):
    trips = parse_schedules(schedules, PZ, KRK)
    orders = {t.order_id for t in trips}
    assert 1005 not in orders  # Kraków przed Poznaniem — inny kierunek
    assert 1006 not in orders  # na stacji A brak odjazdu
    assert orders == {1001, 1002, 1003, 1004, 1007}
    # 1001 kursuje 2 dni -> 2 kursy
    assert len([t for t in trips if t.order_id == 1001]) == 2
    assert trips == sorted(trips, key=lambda t: t.departure)


def test_schedules_reverse_direction(schedules):
    trips = parse_schedules(schedules, KRK, PZ)
    assert [t.order_id for t in trips] == [1005]


def test_schedule_fields(schedules):
    t = next(t for t in parse_schedules(schedules, PZ, KRK) if t.order_id == 1001 and t.operating_date == date(2026, 10, 24))
    assert t.departure == datetime(2026, 10, 24, 14, 5, tzinfo=WARSAW)
    assert t.arrival == datetime(2026, 10, 24, 19, 47, tzinfo=WARSAW)
    assert (t.departure_platform, t.departure_track) == ("3", "6")
    assert (t.arrival_platform, t.arrival_track) == ("4", "7")
    assert (t.category, t.number, t.name, t.carrier_code) == ("IC", "38170", "Przykładowy", "IC")
    assert t.key == (26, 1001, date(2026, 10, 24))


def test_schedule_missing_platform_and_leading_zeros(schedules):
    t = next(t for t in parse_schedules(schedules, PZ, KRK) if t.order_id == 1002)
    assert t.departure_platform is None
    assert t.departure_track is None  # pusty string -> None
    assert t.number == "81600"
    assert t.category == "TLK"
    assert t.name == ""


def test_schedule_day_offsets(schedules):
    trips = {t.order_id: t for t in parse_schedules(schedules, PZ, KRK)}
    night = trips[1003]
    assert night.operating_date == date(2026, 10, 24)
    assert night.departure == datetime(2026, 10, 25, 0, 15, tzinfo=WARSAW)
    assert night.arrival == datetime(2026, 10, 25, 6, 5, tzinfo=WARSAW)
    late = trips[1007]
    assert late.departure == datetime(2026, 10, 24, 19, 20, tzinfo=WARSAW)
    assert late.arrival == datetime(2026, 10, 25, 1, 5, tzinfo=WARSAW)


def test_carrier_dictionaries(schedules, carriers):
    assert parse_carriers(carriers) == {"IC": "„PKP Intercity” Spółka Akcyjna", "PR": "POLREGIO S.A."}
    assert schedule_carrier_names(schedules) == {"IC": "„PKP Intercity” Spółka Akcyjna", "PR": "POLREGIO S.A."}
    assert short_carrier_name("„PKP Intercity” Spółka Akcyjna") == "PKP Intercity"
    assert short_carrier_name("POLREGIO S.A.") == "POLREGIO"


def test_operations_merges_pages(operations_pages):
    live = parse_operations(operations_pages)
    assert set(live) == {(26, o, date(2026, 10, 24)) for o in (1001, 1002, 1004, 5555)}
    t = live[(26, 1001, date(2026, 10, 24))]
    assert t.status == "P"
    s = t.stops[PZ]
    assert s.departure_delay == 7
    assert s.actual_departure == datetime(2026, 10, 24, 14, 12, tzinfo=WARSAW)
    assert not s.confirmed
    assert t.stops[KRK].arrival_delay == 5


def test_operations_null_delay_and_cancel(operations_pages):
    live = parse_operations(operations_pages)
    s = live[(26, 1002, date(2026, 10, 24))].stops[PZ]
    assert s.departure_delay is None  # API pomija 0
    cancelled = live[(26, 1004, date(2026, 10, 24))]
    assert cancelled.status == "X"
    assert cancelled.stops[PZ].cancelled
    assert cancelled.stops[PZ].actual_departure is None


def test_garbage_input_is_tolerated():
    assert parse_schedules(None, PZ, KRK) == []
    assert parse_schedules({"routes": None}, PZ, KRK) == []
    assert parse_operations([None, {"trains": [{"operatingDate": None}]}]) == {}
    assert parse_carriers([]) == {}
