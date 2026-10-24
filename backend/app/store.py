"""SQLite: cache surowych odpowiedzi PLK, stan workera i subskrypcje push.

Surowy JSON trzymamy na dysku, żeby restart kontenera nie zjadał limitu dobowego API.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    fetched_at REAL NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watches (
    subscription_id INTEGER NOT NULL REFERENCES push_subscriptions(id) ON DELETE CASCADE,
    train_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    label TEXT NOT NULL,
    last_delay INTEGER,
    last_platform TEXT,
    last_cancelled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    PRIMARY KEY (subscription_id, train_id)
);
"""


@dataclass(frozen=True)
class CacheEntry:
    fetched_at: float
    body: Any


@dataclass(frozen=True)
class Subscription:
    id: int
    endpoint: str
    p256dh: str
    auth: str

    def as_webpush(self) -> dict:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


@dataclass(frozen=True)
class Watch:
    subscription_id: int
    train_id: str
    direction: str
    label: str
    last_delay: int | None
    last_platform: str | None
    last_cancelled: bool
    created_at: float


class Store:
    def __init__(self, path: str) -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.execute("PRAGMA journal_mode = WAL") if path != ":memory:" else None
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._db.close()

    # --- cache ---
    def put(self, key: str, body: Any, fetched_at: float | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (key, fetched_at, body) VALUES (?, ?, ?)",
                (key, fetched_at if fetched_at is not None else time.time(), json.dumps(body, ensure_ascii=False)),
            )

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            row = self._db.execute("SELECT fetched_at, body FROM cache WHERE key = ?", (key,)).fetchone()
        return CacheEntry(row[0], json.loads(row[1])) if row else None

    # --- meta ---
    def set_meta(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    # --- push ---
    def upsert_subscription(self, endpoint: str, p256dh: str, auth: str) -> int:
        with self._lock:
            self._db.execute(
                "INSERT INTO push_subscriptions (endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth",
                (endpoint, p256dh, auth, time.time()),
            )
            row = self._db.execute("SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        return int(row[0])

    def subscription_by_endpoint(self, endpoint: str) -> Subscription | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
            ).fetchone()
        return Subscription(*row) if row else None

    def subscription(self, sub_id: int) -> Subscription | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE id = ?", (sub_id,)
            ).fetchone()
        return Subscription(*row) if row else None

    def count_subscriptions(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0])

    def delete_subscription(self, sub_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub_id,))

    def add_watch(self, sub_id: int, train_id: str, direction: str, label: str,
                  delay: int | None, platform: str | None, cancelled: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO watches (subscription_id, train_id, direction, label, last_delay, "
                "last_platform, last_cancelled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (sub_id, train_id, direction, label, delay, platform, int(cancelled), time.time()),
            )

    def remove_watch(self, sub_id: int, train_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM watches WHERE subscription_id = ? AND train_id = ?", (sub_id, train_id))

    def watches(self, sub_id: int | None = None) -> list[Watch]:
        sql = ("SELECT subscription_id, train_id, direction, label, last_delay, last_platform, last_cancelled, "
               "created_at FROM watches")
        args: tuple = ()
        if sub_id is not None:
            sql += " WHERE subscription_id = ?"
            args = (sub_id,)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [Watch(r[0], r[1], r[2], r[3], r[4], r[5], bool(r[6]), r[7]) for r in rows]

    def update_watch_state(self, sub_id: int, train_id: str, delay: int | None,
                           platform: str | None, cancelled: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE watches SET last_delay = ?, last_platform = ?, last_cancelled = ? "
                "WHERE subscription_id = ? AND train_id = ?",
                (delay, platform, int(cancelled), sub_id, train_id),
            )
