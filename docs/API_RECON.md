# Etap 1 — rozpoznanie API i plan

Stan na 2026-10-24. Źródło: oficjalna specyfikacja OpenAPI „Open Railway Data API” **v1.1.4**
(`https://pdp-api.plk-sa.pl/swagger/v1/swagger.json`, snapshot z 2026-10-20 w repo
[domel76/open-data-plk-skill](https://github.com/domel76/open-data-plk-skill/blob/main/references/openapi.json))
oraz trzy działające klienty open source (niżej). Host `pdp-api.plk-sa.pl` był
zablokowany w środowisku, w którym to powstało, więc **nic tu nie jest jeszcze sprawdzone
żywym zapytaniem** — pierwsza rzecz po dostaniu klucza to zrzut prawdziwych odpowiedzi.

## ID stacji

| Stacja | ID PLK (`stationId`) | IBNR | Źródła |
|---|---|---|---|
| Poznań Główny | **30601** | 159 | `geo.osm` (tag `ref`) i `directions.yaml` w PolishTrainsGTFS |
| Kraków Główny | **80416** | 85 | j.w. |

Dwa niezależne miejsca w tym samym projekcie (dane OSM i ręczne reguły kierunków) dają te same ID.
Do potwierdzenia jednym zapytaniem: `GET /api/v1/dictionaries/stations?search=Poznań Główny`.

## Endpointy, których użyjemy

| Endpoint | Po co | Kluczowe parametry |
|---|---|---|
| `GET /api/v1/schedules` | rozkład + **peron/tor** | `dateFrom`, `dateTo` (yyyy-MM-dd), `fromStations=30601&toStations=80416` (i odwrotnie) — filtr kierunkowy wbudowany w API; `fullRoute=false` przycina trasę do odcinka from→to |
| `GET /api/v1/operations` | realizacja, opóźnienia | `stations=30601,80416`, `withPlanned=true`, `pageSize` (max 5000) |
| `GET /api/v1/data-version` | tani test, czy PLK opublikował nowe dane | — |
| `GET /api/v1/dictionaries/carriers` | pełne nazwy przewoźników | — |

Każdy endpoint ma też wariant `/shortened` (skrócone nazwy pól, mniejsze odpowiedzi).
Przy filtrze na 2 stacje rozmiar nie jest problemem, więc **zaczynamy od pełnych nazw pól** (czytelniejszy kod i testy).

## Pola, które nas interesują

**Rozkład** — `RouteDto`: `scheduleId`, `orderId`, `trainOrderId`, `name`, `carrierCode`,
`nationalNumber`, `internationalDepartureNumber`, `commercialCategorySymbol` (np. IC, TLK, EIP),
`operatingDates[]`, `stations[]`.
`StationOnRouteDto`: `stationId`, `orderNumber`, `departureTime` / `arrivalTime` (TimeSpan, np. `"14:05:00"`),
`departureDay` / `arrivalDay` (przesunięcie dni), **`departurePlatform`, `departureTrack`,
`arrivalPlatform`, `arrivalTrack`**, `departureTrainNumber`, `stopTypeId`.

**Realizacja** — `TrainOperationDto`: `scheduleId`, `orderId`, `trainOrderId`, `operatingDate`,
`trainStatus` (S = nie wyruszył, P = w trasie, C = zakończony, X = odwołany, Q = częściowo odwołany), `stations[]`.
`OperationStationDto`: `stationId`, `plannedDeparture`, `actualDeparture` (rzeczywisty lub prognozowany),
`departureDelayMinutes`, `arrivalDelayMinutes` (tylko z `withPlanned=true`; brak = brak danych / 0),
`isConfirmed`, `isCancelled`.

Łączenie: klucz `(scheduleId, orderId, operatingDate)` z rozkładu ↔ realizacji.

## Peron / tor — najważniejsze ustalenie

- **Peron i tor są tylko w rozkładzie** (`/schedules`). W odpowiedzi `/operations` takich pól nie ma
  (sprawdzone w specyfikacji: `*Platform`/`*Track` występują wyłącznie w `StationOnRouteDto`).
- Wniosek: PLK da nam **planowy** peron/tor; zmiany peronu w dniu kursowania nie będą widoczne.
  Potwierdzają to README PolishTrainsGTFS („PKP PLK's API doesn't have live platform and track data”)
  i michal-krol/delay_monitor.
- KOLEO jako zapas na żywe perony: host `koleo.pl` też był zablokowany, więc nie sprawdziłem
  jego API ani ID stacji. To nieoficjalne API — zostawiam jako opcjonalny moduł do weryfikacji później.

## Czas

- `generatedAt` i `data-version.timestamp` są w UTC.
- Czasy pociągów (`plannedDeparture`, `actualDeparture`) to czas lokalny Europe/Warsaw bez strefy
  (spec: „UTC nieoznaczone”; klient mat-szymanski/pociagi traktuje je jako lokalne).
  Backend będzie je interpretował jako `Europe/Warsaw` (`zoneinfo`) — do potwierdzenia na prawdziwym zrzucie.
- Pociągi nocne: `departureDay` > 0 → data odjazdu = `operatingDate` + dni.

## Limity — inne niż zakładaliśmy

Ze specyfikacji:

| Pakiet | Na godzinę | Na dobę |
|---|---|---|
| Basic | 100 | **1 000** |
| Standard | 500 | 5 000 |
| Premium | 2 000 | 20 000 |

Limit dobowy Basic to średnio 1 zapytanie na **86 s**, więc odpytywanie co 60 s (1 440/dobę) go przekroczy.
Plan budżetu na Basic:

- rozkład: 2 zapytania raz na dobę (oba kierunki, wczoraj–jutro) + przy starcie;
- realizacja: 1 zapytanie co **120 s** (domyślnie, konfigurowalne) = ~720/dobę;
- przerwa nocna (np. 01:00–04:30, konfigurowalna), gdy nic nie jedzie;
- odczyt nagłówków `X-RateLimit-*` i zwalnianie przy zbliżaniu się do limitu.

API ma `POST /api/v1/apikey/limit-request` (wniosek o Standard) — wtedy można zejść do 60 s.
Frontend i tak odświeża co 60 s, ale z cache backendu.

## Projekty, z których brałem nazwy pól i zachowanie API

- [domel76/open-data-plk-skill](https://github.com/domel76/open-data-plk-skill) — snapshot specyfikacji OpenAPI.
- [JanKemnitz/PolishTrainsGTFS](https://github.com/JanKemnitz/PolishTrainsGTFS) — ID stacji, użycie `/schedules` i `/operations`, perony z rozkładu.
- [mat-szymanski/pociagi](https://github.com/mat-szymanski/pociagi) — działający klient w Pythonie (`/data-version`, `withPlanned`, puste pola opóźnienia przy 0).
- [michal-krol/delay_monitor](https://github.com/michal-krol/delay_monitor) — perony tylko w rozkładzie.

## Plan

**Backend (etap 2)** — `backend/`, Python 3.12 + FastAPI + httpx, Docker.
1. `plk/client.py` — klient z `X-API-Key` z `PLK_API_KEY`, timeouty, ponawianie przy 5xx, odczyt `X-RateLimit-*`.
2. `plk/parse.py` — czyste funkcje: JSON → modele (`ScheduledDeparture`, `LiveStatus`); testy na zapisanych JSON-ach w `tests/fixtures/`.
3. `worker.py` — pętla w tle (asyncio): rozkład raz na dobę, realizacja co `PLK_POLL_SECONDS` (domyślnie 120), przerwa nocna, backoff przy 429.
4. Cache w SQLite (przeżywa restart kontenera, ważne przy limicie dobowym): ostatni rozkład, ostatnia realizacja, czas pobrania.
5. `GET /api/departures?direction=pz-krk|krk-pz` → najbliższe ~4 h: godzina planowa, rzeczywista/prognozowana, opóźnienie, numer, nazwa, kategoria, przewoźnik, peron/tor (`null` → „—”), status (odwołany), plus `dataAge` / `fetchedAt` i `stale`.
6. `GET /api/health`, `docker-compose.yml`, `.env.example`, README (lokalnie + VPS za reverse proxy).

**Frontend (etap 3)** — statyczne pliki serwowane przez FastAPI, bez frameworka:
dwie kolumny na iPadzie / zakładki na iPhonie, tryb ciemny, duże liczby, kolory opóźnień
(0 zielony, 1–15 żółty, >15 czerwony), „dane sprzed X min”, odświeżanie co 60 s, pull-to-refresh,
manifest, service worker, ikony (w tym `apple-touch-icon`).

**Faza 2** — Web Push (VAPID, `pywebpush`), „obserwuj pociąg”, powiadomienie przy zmianie opóźnienia o ≥5 min.
Uwaga: skoro peron PLK jest planowy, „pojawił się peron” zadziała sensownie dopiero z żywym źródłem (KOLEO).

## Do sprawdzenia, gdy będzie klucz i dostęp sieciowy

1. `dictionaries/stations?search=...` — potwierdzić 30601 i 80416.
2. Zapisać prawdziwe odpowiedzi `/schedules` i `/operations` jako fixtures testów.
3. Czy `departurePlatform` jest wypełniony dla Poznania i Krakowa (dla IC i Polregio).
4. Format czasów w `/operations` (strefa) i zachowanie `departureDelayMinutes` przy 0.
5. Czy `stations=30601,80416` w `/operations` zwraca pociągi, które jeszcze nie wyruszyły (status S).
