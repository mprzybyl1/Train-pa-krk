import asyncio
import json
from datetime import datetime

from app.plk.parse import WARSAW
from app.push import Notifier, detect_change, label_for, platform_label
from app.store import Watch
from app.worker import Worker

from .helpers import FakeSource, make_service


def _dep(service, train="26-1001-2026-10-24"):
    return next(d for d in service.board("pz-krk") if d.id == train)


def _watch(delay=None, platform=None, cancelled=False):
    return Watch(1, "x", "pz-krk", "IC 38170", delay, platform, cancelled, 0)


def test_detect_change_rules():
    service, _ = make_service()
    asyncio.run(Worker(service, FakeSource()).tick())
    d = _dep(service)  # opóźnienie 7, peron 3 tor 6
    plat = platform_label(d)
    assert plat == "peron 3, tor 6"
    assert detect_change(_watch(delay=0, platform=plat), d, 5).body.startswith("Opóźnienie 7 min")
    assert detect_change(_watch(delay=4, platform=plat), d, 5) is None  # zmiana o 3 < 5
    assert detect_change(_watch(delay=7, platform=None), d, 5).body == "Podano peron 3, tor 6"
    assert detect_change(_watch(delay=7, platform="peron 1"), d, 5).body == "Zmiana: peron 3, tor 6"
    cancelled = _dep(service, "26-1004-2026-10-24")
    assert detect_change(_watch(), cancelled, 5).body == "Pociąg odwołany."
    assert detect_change(_watch(cancelled=True), cancelled, 5) is None
    assert label_for(d) == "IC 38170 Przykładowy"


def test_notifier_sends_once_and_cleans_up():
    service, clock = make_service()
    store = service.store
    src = FakeSource()
    w = Worker(service, src)
    asyncio.run(w.tick())
    sub = store.upsert_subscription("https://push.example/1", "p", "a")
    store.add_watch(sub, "26-1001-2026-10-24", "pz-krk", "IC 38170", 0, "peron 3, tor 6", False)
    sent = []
    notifier = Notifier(store, lambda s, p: sent.append(json.loads(p)) or True, 5)
    assert asyncio.run(notifier.check(service)) == 1
    assert sent[0]["title"] == "IC 38170 (14:05)"
    assert asyncio.run(notifier.check(service)) == 0  # stan zapamiętany
    # pociąg odjechał -> obserwacja usunięta
    clock.t = datetime(2026, 10, 24, 14, 20, tzinfo=WARSAW).timestamp()
    asyncio.run(notifier.check(service))
    assert store.watches() == []


def test_expired_subscription_removed():
    service, _ = make_service()
    store = service.store
    asyncio.run(Worker(service, FakeSource()).tick())
    sub = store.upsert_subscription("https://push.example/2", "p", "a")
    store.add_watch(sub, "26-1001-2026-10-24", "pz-krk", "IC", 0, None, False)
    asyncio.run(Notifier(store, lambda s, p: False, 5).check(service))
    assert store.subscription(sub) is None
    assert store.watches() == []
