from datetime import datetime, timedelta

from app.departures import build_board
from app.plk.parse import WARSAW, parse_carriers, parse_operations, parse_schedules

PZ, KRK = 30601, 80416


def board(schedules, operations_pages, carriers, now, hours=4):
    return build_board(parse_schedules(schedules, PZ, KRK), parse_operations(operations_pages),
                       parse_carriers(carriers), now, timedelta(hours=hours), PZ, KRK)


def at(h, m, day=24):
    return datetime(2026, 10, day, h, m, tzinfo=WARSAW)


def test_board_window_and_order(schedules, operations_pages, carriers):
    deps = board(schedules, operations_pages, carriers, at(13, 30))
    assert [d.id for d in deps] == ["26-1001-2026-10-24", "26-1002-2026-10-24", "26-1004-2026-10-24"]
    # 19:20 jest poza oknem 4 h od 13:30, kurs 1001 z 25.10 też


def test_delayed_train(schedules, operations_pages, carriers):
    d = board(schedules, operations_pages, carriers, at(13, 30))[0]
    assert d.delay == 7 and d.live
    assert d.expected_departure == at(14, 12)
    assert d.planned_departure == at(14, 5)
    assert d.expected_arrival == at(19, 52)
    assert d.arrival_delay == 5
    assert (d.platform, d.track) == ("3", "6")
    assert d.carrier_name == "PKP Intercity"
    assert d.to_json()["expectedDeparture"] == "2026-10-24T14:12:00+02:00"


def test_null_delay_with_actual_time_means_on_time(schedules, operations_pages, carriers):
    d = next(x for x in board(schedules, operations_pages, carriers, at(13, 30)) if x.id.startswith("26-1002"))
    assert d.delay == 0 and d.live
    assert d.platform is None


def test_cancelled(schedules, operations_pages, carriers):
    d = next(x for x in board(schedules, operations_pages, carriers, at(13, 30)) if x.id.startswith("26-1004"))
    assert d.cancelled
    assert d.delay is None and not d.live
    assert d.expected_departure == at(16, 0)


def test_no_live_data_falls_back_to_plan(schedules, carriers):
    deps = board(schedules, [], carriers, at(13, 30))
    assert deps and all(d.delay is None and not d.live for d in deps)
    assert deps[0].expected_departure == deps[0].planned_departure


def test_delayed_train_stays_after_planned_time(schedules, operations_pages, carriers):
    ids = [d.id for d in board(schedules, operations_pages, carriers, at(14, 9))]
    assert "26-1001-2026-10-24" in ids  # planowo 14:05, ale prognoza 14:12
    ids = [d.id for d in board(schedules, operations_pages, carriers, at(14, 15))]
    assert "26-1001-2026-10-24" not in ids


def test_night_train_after_midnight(schedules, operations_pages, carriers):
    deps = board(schedules, operations_pages, carriers, at(22, 0))
    assert [d.id for d in deps] == ["26-1003-2026-10-24"]
    assert deps[0].planned_departure == at(0, 15, day=25)


def test_departed_confirmed_train_disappears():
    from app.plk.parse import LiveStop, LiveTrain, ScheduledTrip
    from datetime import date
    trip = ScheduledTrip(26, 1, date(2026, 10, 24), "IC", "IC", "1", "", at(14, 0), None, None, None)
    stop = LiveStop(PZ, None, at(14, 0), None, at(14, 1), None, 1, True, False)
    live = {trip.key: LiveTrain(26, 1, date(2026, 10, 24), "P", {PZ: stop})}
    assert build_board([trip], live, {}, at(14, 2), timedelta(hours=4), PZ, KRK)[0].departed
    assert build_board([trip], live, {}, at(14, 4), timedelta(hours=4), PZ, KRK) == []
