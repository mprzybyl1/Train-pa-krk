"""Faza 2: Web Push (VAPID) dla obserwowanych pociągów.

Powiadomienie wysyłamy, gdy względem ostatnio zgłoszonego stanu:
- opóźnienie zmieniło się o >= PUSH_DELAY_THRESHOLD minut,
- pojawił się albo zmienił się peron/tor,
- pociąg został odwołany.
Obserwacja kończy się sama, gdy pociąg zniknie z tablicy (odjechał).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Callable

from .departures import Departure
from .service import DIRECTIONS, TrainService
from .store import Store, Watch

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Change:
    title: str
    body: str
    delay_changed: bool = False  # tylko wtedy przesuwamy punkt odniesienia opóźnienia


def platform_label(d: Departure) -> str | None:
    if not d.platform and not d.track:
        return None
    parts = []
    if d.platform:
        parts.append(f"peron {d.platform}")
    if d.track:
        parts.append(f"tor {d.track}")
    return ", ".join(parts)


def detect_change(watch: Watch, d: Departure, threshold: int) -> Change | None:
    hhmm = d.planned_departure.strftime("%H:%M")
    title = f"{watch.label} ({hhmm})"
    if d.cancelled:  # po odwołaniu nic więcej nie wysyłamy
        return None if watch.last_cancelled else Change(title, "Pociąg odwołany.")
    msgs = []
    delay_changed = False
    plat = platform_label(d)
    if plat and plat != watch.last_platform:
        msgs.append(("Zmiana: " if watch.last_platform else "Podano ") + plat)
    if d.delay is not None:
        prev = watch.last_delay or 0
        if abs(d.delay - prev) >= threshold:
            delay_changed = True
            if d.delay <= 0:
                msgs.append("Opóźnienie odrobione — odjazd planowo.")
            else:
                msgs.append(f"Opóźnienie {d.delay} min, odjazd ok. {d.expected_departure.strftime('%H:%M')}.")
    if not msgs:
        return None
    return Change(title, " ".join(msgs), delay_changed)


def label_for(d: Departure) -> str:
    return " ".join(x for x in (d.category, d.number, d.name) if x) or "Pociąg"


Sender = Callable[[dict, str], bool]  # (subskrypcja, JSON) -> False = subskrypcja wygasła


def make_sender(private_key: str, subject: str) -> Sender:
    from pywebpush import WebPushException, webpush

    def send(subscription: dict, payload: str) -> bool:
        try:
            webpush(subscription, payload, vapid_private_key=private_key,
                    vapid_claims={"sub": subject}, ttl=900)
            return True
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                return False
            log.warning("Web push nie wysłany: %s", exc)
            return True

    return send


class Notifier:
    def __init__(self, store: Store, sender: Sender, threshold: int) -> None:
        self.store = store
        self.sender = sender
        self.threshold = threshold

    async def check(self, service: TrainService) -> int:
        """Porównuje obserwowane pociągi z bieżącą tablicą; zwraca liczbę wysłanych powiadomień."""
        watches = self.store.watches()
        if not watches:
            return 0
        boards = {d: {x.id: x for x in service.board(d)} for d in DIRECTIONS}
        sent = 0
        for w in watches:
            board = boards.get(w.direction) or {}
            if not board:  # brak danych (np. pusty cache) — nie kasujemy obserwacji na ślepo
                continue
            dep = board.get(w.train_id)
            if dep is None or dep.departed:
                self.store.remove_watch(w.subscription_id, w.train_id)
                continue
            change = detect_change(w, dep, self.threshold)
            if change is None:
                continue
            sub = self.store.subscription(w.subscription_id)
            if sub is None:
                continue
            payload = json.dumps({"title": change.title, "body": change.body,
                                  "tag": w.train_id, "url": f"/?direction={w.direction}"}, ensure_ascii=False)
            alive = await asyncio.to_thread(self.sender, sub.as_webpush(), payload)
            if not alive:
                self.store.delete_subscription(sub.id)
                continue
            sent += 1
            new_delay = dep.delay if change.delay_changed else w.last_delay
            self.store.update_watch_state(w.subscription_id, w.train_id, new_delay,
                                          platform_label(dep) or w.last_platform, dep.cancelled)
        return sent
