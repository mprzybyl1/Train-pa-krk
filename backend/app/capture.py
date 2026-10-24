"""Jednorazowe sprawdzenie API i zapis prawdziwych odpowiedzi jako fixtures.

    PLK_API_KEY=... python -m app.capture [katalog]   (domyślnie tests/fixtures/real)

Zużywa ok. 6 zapytań z limitu. Wypisuje ID stacji z wyszukiwarki PLK, żeby potwierdzić 30601/80416,
oraz czy w rozkładzie są perony/tory.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import load_settings
from .plk.client import PlkClient
from .plk.parse import parse_operations, parse_schedules


async def main(out: Path) -> None:
    s = load_settings()
    client = PlkClient(s.plk_api_key, s.plk_base_url)
    out.mkdir(parents=True, exist_ok=True)

    def save(name: str, body) -> None:
        (out / name).write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  zapisano {out / name}")

    try:
        for q in ("Poznań Główny", "Kraków Główny"):
            res = await client.get("/api/v1/dictionaries/stations", {"search": q, "pageSize": "20"})
            stations = res.body.get("stations") if isinstance(res.body, dict) else res.body
            print(f"[stacje '{q}']", json.dumps(stations, ensure_ascii=False)[:500])
            save(f"stations_{q.split()[0].lower()}.json", res.body)

        today = date.today()
        for name, a, b in (("pz_krk", s.poznan_id, s.krakow_id), ("krk_pz", s.krakow_id, s.poznan_id)):
            res = await client.schedules(a, b, today, today + timedelta(days=1))
            save(f"schedules_{name}.json", res.body)
            trips = parse_schedules(res.body, a, b)
            with_plat = sum(1 for t in trips if t.departure_platform)
            print(f"[rozkład {name}] kursów: {len(trips)}, z peronem: {with_plat}")

        res = await client.operations([s.poznan_id, s.krakow_id])
        for i, page in enumerate(res.body, 1):
            save(f"operations_page{i}.json", page)
        live = parse_operations(res.body)
        print(f"[realizacja] pociągów: {len(live)}, limity: {res.limits}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parents[1] / "tests" / "fixtures" / "real"
    asyncio.run(main(target))
