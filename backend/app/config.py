"""Konfiguracja z zmiennych środowiskowych (patrz .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "tak"}


def _parse_quiet_hours(raw: str) -> tuple[time, time] | None:
    """'01:00-04:30' -> (01:00, 04:30). Puste -> brak przerwy."""
    if not raw:
        return None
    start, _, end = raw.partition("-")
    return time.fromisoformat(start.strip()), time.fromisoformat(end.strip())


@dataclass(frozen=True)
class Settings:
    plk_api_key: str = ""
    plk_base_url: str = "https://pdp-api.plk-sa.pl"
    poll_seconds: int = 120
    schedule_refresh_hours: float = 12
    quiet_hours: tuple[time, time] | None = None
    poznan_id: int = 30601
    krakow_id: int = 80416
    window_hours: float = 4
    db_path: str = "data/cache.sqlite3"
    worker_enabled: bool = True
    demo: bool = False
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:admin@example.com"
    push_delay_threshold: int = 5
    static_dir: str = field(default="")

    @property
    def push_enabled(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def stale_after_seconds(self) -> int:
        # dane uznajemy za nieświeże po 3 nieudanych/pominiętych cyklach
        return max(self.poll_seconds * 3, 300)


def load_settings() -> Settings:
    here = os.path.dirname(os.path.abspath(__file__))
    default_static = os.path.normpath(os.path.join(here, "..", "..", "frontend"))
    return Settings(
        plk_api_key=_env("PLK_API_KEY"),
        plk_base_url=_env("PLK_BASE_URL", "https://pdp-api.plk-sa.pl").rstrip("/"),
        poll_seconds=int(_env("PLK_POLL_SECONDS", "120")),
        schedule_refresh_hours=float(_env("PLK_SCHEDULE_REFRESH_HOURS", "12")),
        quiet_hours=_parse_quiet_hours(_env("PLK_QUIET_HOURS", "01:00-04:30")),
        poznan_id=int(_env("STATION_POZNAN_ID", "30601")),
        krakow_id=int(_env("STATION_KRAKOW_ID", "80416")),
        window_hours=float(_env("WINDOW_HOURS", "4")),
        db_path=_env("DB_PATH", "data/cache.sqlite3"),
        worker_enabled=_bool("WORKER_ENABLED", True),
        demo=_bool("DEMO_MODE", False),
        vapid_public_key=_env("VAPID_PUBLIC_KEY"),
        vapid_private_key=_env("VAPID_PRIVATE_KEY"),
        vapid_subject=_env("VAPID_SUBJECT", "mailto:admin@example.com"),
        push_delay_threshold=int(_env("PUSH_DELAY_THRESHOLD", "5")),
        static_dir=_env("STATIC_DIR", default_static),
    )
